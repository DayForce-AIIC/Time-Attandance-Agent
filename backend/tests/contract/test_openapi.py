"""OpenAPI synchronization, frozen-contract, and live GET response tests."""

import json
from pathlib import Path

from app.main import app
from app.policies import FROZEN_RULE_IDS as POLICY_RULE_IDS
from app.schemas import (
    Action,
    AuditEventOut,
    AuditVerifyOut,
    ExceptionDetail,
    ExceptionOut,
    ExceptionStatus,
    FaultMode,
    HealthOut,
    LLMModeOut,
    MetricsOut,
    PolicyPassage,
    TimecardOut,
)
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[3]
OPENAPI_PATH = ROOT / "docs" / "openapi.json"

FROZEN_RULE_IDS = [
    "PUNCH-01",
    "PUNCH-02",
    "PUNCH-03",
    "PUNCH-04",
    "PUNCH-99",
    "MAX-01",
    "BRK-01",
    "BRK-02",
    "OT-01",
    "OT-02",
    "OT-03",
    "SCHED-01",
    "SCHED-02",
    "REST-01",
    "DAY-01",
]


def test_openapi_file_matches_application_schema():
    committed = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert committed == app.openapi(), "OpenAPI is out of date; run `make openapi`."


def test_every_phase_11_route_has_the_expected_response_model():
    schema = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    expected = {
        ("/api/v1/health", "get"): "HealthOut",
        ("/api/v1/detect", "post"): "DetectOut",
        ("/api/v1/exceptions", "get"): "ExceptionOut",
        ("/api/v1/exceptions/{exception_id}", "get"): "ExceptionDetail",
        ("/api/v1/exceptions/{exception_id}/propose", "post"): "ProposalOut",
        ("/api/v1/exceptions/{exception_id}/decision", "post"): "DecisionOut",
        ("/api/v1/exceptions/{exception_id}/audit", "get"): "AuditEventOut",
        ("/api/v1/employees/{employee_id}/timecard", "get"): "TimecardOut",
        ("/api/v1/policies", "get"): "PolicyPassage",
        ("/api/v1/policies/{policy_id}", "get"): "PolicyPassage",
        ("/api/v1/audit/verify", "get"): "AuditVerifyOut",
        ("/api/v1/metrics", "get"): "MetricsOut",
        ("/api/v1/admin/llm-mode", "get"): "LLMModeOut",
        ("/api/v1/admin/llm-mode", "post"): "LLMModeOut",
        ("/api/v1/admin/reset-demo", "post"): "DetectOut",
    }
    for (path, method), expected_name in expected.items():
        assert method in schema["paths"][path]
        response_schema = schema["paths"][path][method]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        if response_schema.get("type") == "array":
            response_schema = response_schema["items"]
        assert response_schema["$ref"].endswith(f"/{expected_name}")


def test_live_get_responses_match_pydantic_and_openapi_json_schema(client):
    exception = next(
        row for row in client.get("/api/v1/exceptions").json() if row["employee_id"] == "e-001"
    )
    exception_id = exception["id"]
    responses = [
        ("/api/v1/health", HealthOut),
        ("/api/v1/exceptions", list[ExceptionOut]),
        (f"/api/v1/exceptions/{exception_id}", ExceptionDetail),
        (f"/api/v1/exceptions/{exception_id}/audit", list[AuditEventOut]),
        ("/api/v1/employees/e-001/timecard", TimecardOut),
        ("/api/v1/policies", list[PolicyPassage]),
        ("/api/v1/policies/PUNCH-01", PolicyPassage),
        ("/api/v1/audit/verify", AuditVerifyOut),
        ("/api/v1/metrics", MetricsOut),
        ("/api/v1/admin/llm-mode", LLMModeOut),
    ]
    openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    for path, model in responses:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert response.headers.get("X-Request-ID")
        payload = response.json()
        if isinstance(model, list) or getattr(model, "__origin__", None) is list:
            TypeAdapter(model).validate_python(payload)
        else:
            model.model_validate(payload)
        route_path = path
        if path.startswith("/api/v1/exceptions/"):
            route_path = "/api/v1/exceptions/{exception_id}"
            if path.endswith("/audit"):
                route_path += "/audit"
        elif path.startswith("/api/v1/employees/"):
            route_path = "/api/v1/employees/{employee_id}/timecard"
        elif path.startswith("/api/v1/policies/"):
            route_path = "/api/v1/policies/{policy_id}"
        response_schema = openapi["paths"][route_path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        resolved_schema = _resolve_component_refs(response_schema, openapi["components"]["schemas"])
        Draft202012Validator(resolved_schema).validate(payload)


def _resolve_component_refs(schema, components):
    """Inline OpenAPI component references before validating the JSON payload."""
    if isinstance(schema, list):
        return [_resolve_component_refs(item, components) for item in schema]
    if not isinstance(schema, dict):
        return schema
    reference = schema.get("$ref")
    if reference is not None:
        prefix = "#/components/schemas/"
        assert reference.startswith(prefix)
        target = components[reference.removeprefix(prefix)]
        resolved = _resolve_component_refs(target, components)
        siblings = {
            key: _resolve_component_refs(value, components)
            for key, value in schema.items()
            if key != "$ref"
        }
        return {**resolved, **siblings}
    return {key: _resolve_component_refs(value, components) for key, value in schema.items()}


def test_frozen_public_enums_and_rule_ids():
    assert [value.value for value in Action] == [
        "ADD_PUNCH",
        "MODIFY_PUNCH",
        "REMOVE_PUNCH",
        "ADJUST_SCHEDULE",
        "FLAG_FOR_APPROVAL",
        "ESCALATE",
    ]
    assert [value.value for value in FaultMode] == [
        "none",
        "bad_then_good",
        "always_bad",
        "garbage",
        "timeout",
        "bad_citation",
        "naive_latest",
    ]
    assert [value.value for value in ExceptionStatus] == [
        "OPEN",
        "PENDING_REVIEW",
        "ESCALATED",
        "RESOLVED",
        "REJECTED",
        "SUPERSEDED",
        "WRITEBACK_PENDING",
        "WRITEBACK_FAILED",
    ]
    assert list(POLICY_RULE_IDS) == FROZEN_RULE_IDS
