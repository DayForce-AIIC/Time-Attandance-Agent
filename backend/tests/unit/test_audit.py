"""Hash-chain audit unit tests."""

import sqlite3

from app.audit import append_event, verify_chain
from app.db import init_db


def test_audit_events_verify_and_detect_tampering():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    append_event(conn, "trace-a", "ONE", {"value": 1}, exception_id=7)
    append_event(conn, "trace-a", "TWO", {"value": 2}, exception_id=7)
    assert verify_chain(conn).model_dump() == {
        "ok": True,
        "events_checked": 2,
        "first_bad_event_id": None,
    }
    conn.execute("DROP TRIGGER audit_no_update")
    conn.execute("UPDATE audit_log SET payload_json = '{}' WHERE id = 1")
    invalid = verify_chain(conn)
    assert not invalid.ok
    assert invalid.first_bad_event_id == 1
    conn.close()


def test_audit_event_starts_a_fresh_trace_sequence(conn):
    first = append_event(conn, "trace-one", "A", {})
    second = append_event(conn, "trace-two", "B", {})
    assert first.seq == 1
    assert second.seq == 1
    assert second.prev_hash == first.hash
