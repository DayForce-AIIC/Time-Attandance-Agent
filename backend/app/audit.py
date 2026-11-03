"""Append-only, hash-chained audit events stored in SQLite."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.schemas import AuditVerifyOut


GENESIS_HASH = "GENESIS"

EXCEPTION_DETECTED = "EXCEPTION_DETECTED"
PROMPT_BUILT = "PROMPT_BUILT"
LLM_CALL = "LLM_CALL"
LLM_ERROR = "LLM_ERROR"
SCHEMA_VALIDATION = "SCHEMA_VALIDATION"
VALIDATION_RESULT = "VALIDATION_RESULT"
RETRY = "RETRY"
FALLBACK = "FALLBACK"
PROPOSAL_CREATED = "PROPOSAL_CREATED"
DECISION_MADE = "DECISION_MADE"
CORRECTION_APPLIED = "CORRECTION_APPLIED"
EXCEPTION_SUPERSEDED = "EXCEPTION_SUPERSEDED"
FAULT_MODE_CHANGED = "FAULT_MODE_CHANGED"


@dataclass(frozen=True)
class AuditEvent:
    """Persisted audit event returned by :func:`append_event`."""

    id: int
    trace_id: str
    exception_id: int | None
    seq: int
    event_type: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str
    created_at: str


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _event_hash(
    prev_hash: str,
    trace_id: str,
    exception_id: int | None,
    seq: int,
    event_type: str,
    payload_json: str,
    created_at: str,
) -> str:
    material = (
        f"{prev_hash}|{trace_id}|{exception_id}|{seq}|{event_type}|"
        f"{payload_json}|{created_at}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def append_event(
    conn: sqlite3.Connection,
    trace_id: str,
    event_type: str,
    payload: dict[str, Any],
    exception_id: int | None = None,
) -> AuditEvent:
    """Append an event, linking it to the prior global event by hash."""
    payload_json = _canonical_json(payload)
    created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )

    def persist() -> tuple[int, str, str, int]:
        seq_row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM audit_log WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        previous = conn.execute(
            "SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        seq = int(seq_row["next_seq"])
        prev_hash = str(previous["hash"]) if previous else GENESIS_HASH
        digest = _event_hash(
            prev_hash, trace_id, exception_id, seq, event_type, payload_json, created_at
        )
        cursor = conn.execute(
            """INSERT INTO audit_log
               (trace_id, exception_id, seq, event_type, payload_json,
                prev_hash, hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trace_id,
                exception_id,
                seq,
                event_type,
                payload_json,
                prev_hash,
                digest,
                created_at,
            ),
        )
        return seq, prev_hash, digest, int(cursor.lastrowid)

    if conn.in_transaction:
        seq, prev_hash, digest, event_id = persist()
    else:
        with conn:
            seq, prev_hash, digest, event_id = persist()
    return AuditEvent(
        id=event_id,
        trace_id=trace_id,
        exception_id=exception_id,
        seq=seq,
        event_type=event_type,
        payload=payload,
        prev_hash=prev_hash,
        hash=digest,
        created_at=created_at,
    )


def verify_chain(conn: sqlite3.Connection) -> AuditVerifyOut:
    """Recompute the global chain and return the first event that fails."""
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    expected_prev = GENESIS_HASH
    for row in rows:
        event_id = int(row["id"])
        if row["prev_hash"] != expected_prev:
            return AuditVerifyOut(ok=False, events_checked=len(rows), first_bad_event_id=event_id)
        try:
            payload = json.loads(row["payload_json"])
            payload_json = _canonical_json(payload)
            expected_hash = _event_hash(
                str(row["prev_hash"]),
                str(row["trace_id"]),
                row["exception_id"],
                int(row["seq"]),
                str(row["event_type"]),
                payload_json,
                str(row["created_at"]),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return AuditVerifyOut(ok=False, events_checked=len(rows), first_bad_event_id=event_id)
        if payload_json != row["payload_json"] or expected_hash != row["hash"]:
            return AuditVerifyOut(ok=False, events_checked=len(rows), first_bad_event_id=event_id)
        expected_prev = str(row["hash"])
    return AuditVerifyOut(ok=True, events_checked=len(rows), first_bad_event_id=None)
