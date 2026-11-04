"""End-to-end manager API workflow acceptance tests."""

from pathlib import Path

from app.config import Settings
from app.main import create_app
from app.schemas import FaultMode
from fastapi.testclient import TestClient


def test_complete_manager_review_flow_and_visibility(client):
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.headers.get("X-Request-ID")
    assert client.post("/api/v1/detect", json={}).status_code == 200

    manager_one = client.get("/api/v1/exceptions")
    manager_two = client.get("/api/v1/exceptions", headers={"X-Manager-Id": "m-002"})
    assert len(manager_one.json()) == 9
    assert len(manager_two.json()) == 6
    case = next(
        row
        for row in manager_one.json()
        if row["employee_id"] == "e-001" and row["rule_id"] == "PUNCH-01"
    )
    exception_id = case["id"]
    assert client.get(f"/api/v1/exceptions/{exception_id}").status_code == 200
    proposal = client.post(f"/api/v1/exceptions/{exception_id}/propose")
    assert proposal.status_code == 200, proposal.text
    decision = client.post(
        f"/api/v1/exceptions/{exception_id}/decision", json={"action": "approve"}
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["resulting_status"] == "RESOLVED"
    audit = client.get(f"/api/v1/exceptions/{exception_id}/audit")
    assert audit.status_code == 200
    assert any(event["event_type"] == "CORRECTION_APPLIED" for event in audit.json())
    assert client.get("/api/v1/metrics").status_code == 200
    assert client.get("/api/v1/audit/verify").json()["ok"] is True


def test_api_not_found_forbidden_and_request_id(client):
    missing = client.get("/api/v1/exceptions/900001")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"
    case = client.get("/api/v1/exceptions").json()[0]
    forbidden = client.get(f"/api/v1/exceptions/{case['id']}", headers={"X-Manager-Id": "m-002"})
    assert forbidden.status_code == 403
    assert forbidden.headers.get("X-Request-ID")
    supplied = client.get("/api/v1/health", headers={"X-Request-ID": "caller-trace"})
    assert supplied.headers["X-Request-ID"] == "caller-trace"


def test_demo_controls_hidden_when_disabled(tmp_path: Path):
    app = create_app(
        Settings(
            db_path=tmp_path / "disabled.db",
            auto_seed=False,
            enable_demo_controls=False,
        )
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/admin/llm-mode").status_code == 404
        assert client.post("/api/v1/admin/llm-mode", json={"mode": "none"}).status_code == 404
        assert client.post("/api/v1/admin/reset-demo").status_code == 404


def test_fault_fixture_changes_the_live_provider_mode(client, fault):
    fault(client.app, FaultMode.GARBAGE)
    response = client.get("/api/v1/admin/llm-mode")
    assert response.status_code == 200
    assert response.json()["mode"] == "garbage"
