"""Repository layer: all parameterized SQLite statements live in this module."""

import json
import sqlite3
from datetime import date, datetime
from typing import Iterable

from app.engine.shifts import Punch, Sched
from app.schemas import ExceptionStatus, PunchKind, Severity


def count_managers(conn: sqlite3.Connection) -> int:
    """Count seeded managers."""
    row = conn.execute("SELECT COUNT(*) AS total FROM managers").fetchone()
    return int(row["total"])


def count_employees(conn: sqlite3.Connection) -> int:
    """Count employees in the database."""
    row = conn.execute("SELECT COUNT(*) AS total FROM employees").fetchone()
    return int(row["total"])


def insert_manager(conn: sqlite3.Connection, manager_id: str, name: str) -> None:
    """Insert one manager record."""
    conn.execute("INSERT INTO managers (id, name) VALUES (?, ?)", (manager_id, name))


def insert_employee(
    conn: sqlite3.Connection,
    employee_id: str,
    name: str,
    role: str,
    site: str,
    jurisdiction: str,
    manager_id: str,
    ot_eligible: bool,
) -> None:
    """Insert one employee and its manager assignment."""
    conn.execute(
        """INSERT INTO employees
           (id, name, role, site, jurisdiction, manager_id, ot_eligible)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (employee_id, name, role, site, jurisdiction, manager_id, int(ot_eligible)),
    )


def insert_schedule(
    conn: sqlite3.Connection,
    employee_id: str,
    work_date: date,
    start: datetime,
    end: datetime,
    planned_break_minutes: int = 30,
) -> int:
    """Insert a schedule and return its generated ID."""
    cursor = conn.execute(
        """INSERT INTO schedules
           (employee_id, work_date, start_ts, end_ts, planned_break_minutes)
           VALUES (?, ?, ?, ?, ?)""",
        (
            employee_id,
            work_date.isoformat(),
            start.strftime("%Y-%m-%dT%H:%M"),
            end.strftime("%Y-%m-%dT%H:%M"),
            planned_break_minutes,
        ),
    )
    return int(cursor.lastrowid)


def insert_punch(
    conn: sqlite3.Connection,
    employee_id: str,
    work_date: date,
    ts: datetime,
    kind: PunchKind,
    source: str = "device",
) -> int:
    """Insert a punch and return its generated ID."""
    cursor = conn.execute(
        """INSERT INTO punches (employee_id, work_date, ts, kind, source)
           VALUES (?, ?, ?, ?, ?)""",
        (employee_id, work_date.isoformat(), ts.strftime("%Y-%m-%dT%H:%M"), kind.value, source),
    )
    return int(cursor.lastrowid)


def insert_employee_note(
    conn: sqlite3.Connection, employee_id: str, work_date: date, text: str
) -> int:
    """Insert an untrusted employee note and return its ID."""
    cursor = conn.execute(
        "INSERT INTO employee_notes (employee_id, work_date, text) VALUES (?, ?, ?)",
        (employee_id, work_date.isoformat(), text),
    )
    return int(cursor.lastrowid)


def list_employees(
    conn: sqlite3.Connection, employee_ids: Iterable[str] | None = None
) -> list[dict[str, object]]:
    """List employees ordered by ID, optionally restricted to an ID set."""
    if employee_ids is None:
        rows = conn.execute("SELECT * FROM employees ORDER BY id").fetchall()
    else:
        ids = tuple(employee_ids)
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT * FROM employees WHERE id IN ({placeholders}) ORDER BY id", ids
        ).fetchall()
    return [dict(row) for row in rows]


def get_timekeeping_records(
    conn: sqlite3.Connection,
    employee_id: str,
    start_date: date,
    end_date: date,
) -> tuple[list[Punch], list[Sched]]:
    """Load typed punch and schedule records within an inclusive date range."""
    punch_rows = conn.execute(
        """SELECT id, employee_id, work_date, ts, kind, voided FROM punches
           WHERE employee_id = ? AND work_date >= ? AND work_date <= ?
           ORDER BY work_date, ts, id""",
        (employee_id, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    schedule_rows = conn.execute(
        """SELECT id, employee_id, work_date, start_ts, end_ts, planned_break_minutes
           FROM schedules WHERE employee_id = ? AND work_date >= ? AND work_date <= ?
           ORDER BY work_date, id""",
        (employee_id, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    punches = [
        Punch(
            id=int(row["id"]),
            employee_id=str(row["employee_id"]),
            work_date=date.fromisoformat(str(row["work_date"])),
            ts=datetime.strptime(str(row["ts"]), "%Y-%m-%dT%H:%M"),
            kind=PunchKind(str(row["kind"])),
            voided=bool(row["voided"]),
        )
        for row in punch_rows
    ]
    schedules = [
        Sched(
            id=int(row["id"]),
            employee_id=str(row["employee_id"]),
            work_date=date.fromisoformat(str(row["work_date"])),
            start=datetime.strptime(str(row["start_ts"]), "%Y-%m-%dT%H:%M"),
            end=datetime.strptime(str(row["end_ts"]), "%Y-%m-%dT%H:%M"),
            planned_break_minutes=int(row["planned_break_minutes"]),
        )
        for row in schedule_rows
    ]
    return punches, schedules


def get_exception_by_fingerprint(
    conn: sqlite3.Connection, fingerprint: str
) -> sqlite3.Row | None:
    """Find a previously detected exception by its stable fingerprint."""
    return conn.execute(
        "SELECT * FROM exceptions WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()


def get_exception(conn: sqlite3.Connection, exception_id: int) -> sqlite3.Row | None:
    """Load one exception by its primary key."""
    return conn.execute("SELECT * FROM exceptions WHERE id = ?", (exception_id,)).fetchone()


def get_employee(conn: sqlite3.Connection, employee_id: str) -> sqlite3.Row | None:
    """Load an employee by ID."""
    return conn.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()


def get_employee_note(
    conn: sqlite3.Connection, employee_id: str, work_date: date
) -> str | None:
    """Return the most recently recorded note for one employee and work date."""
    row = conn.execute(
        """SELECT text FROM employee_notes
           WHERE employee_id = ? AND work_date = ? ORDER BY id DESC LIMIT 1""",
        (employee_id, work_date.isoformat()),
    ).fetchone()
    return str(row["text"]) if row is not None else None


def create_proposal(
    conn: sqlite3.Connection,
    exception_id: int,
    trace_id: str,
    source: str,
    proposal_json: str,
    validation_json: str,
    attempts: int,
    model: str,
    latency_ms: int,
    created_at: str,
) -> int:
    """Persist one generated proposal and return its database ID."""
    cursor = conn.execute(
        """INSERT INTO proposals
           (exception_id, trace_id, source, proposal_json, validation_json,
            attempts, model, latency_ms, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            exception_id,
            trace_id,
            source,
            proposal_json,
            validation_json,
            attempts,
            model,
            latency_ms,
            created_at,
        ),
    )
    return int(cursor.lastrowid)


def get_latest_proposal(conn: sqlite3.Connection, exception_id: int) -> sqlite3.Row | None:
    """Return the most recently stored proposal for an exception."""
    return conn.execute(
        "SELECT * FROM proposals WHERE exception_id = ? ORDER BY id DESC LIMIT 1",
        (exception_id,),
    ).fetchone()


def get_punch(conn: sqlite3.Connection, punch_id: int) -> sqlite3.Row | None:
    """Load a punch row by ID."""
    return conn.execute("SELECT * FROM punches WHERE id = ?", (punch_id,)).fetchone()


def get_schedule(conn: sqlite3.Connection, schedule_id: int) -> sqlite3.Row | None:
    """Load a schedule row by ID."""
    return conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()


def create_decision(
    conn: sqlite3.Connection,
    exception_id: int,
    proposal_id: int,
    action: str,
    manager_id: str,
    note: str | None,
    final_correction_json: str | None,
    decided_at: str,
) -> int:
    """Persist a manager decision and return its primary key."""
    cursor = conn.execute(
        """INSERT INTO decisions
           (exception_id, proposal_id, action, manager_id, note,
            final_correction_json, decided_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            exception_id,
            proposal_id,
            action,
            manager_id,
            note,
            final_correction_json,
            decided_at,
        ),
    )
    return int(cursor.lastrowid)


def update_punch_ts(conn: sqlite3.Connection, punch_id: int, ts: str) -> None:
    """Update one punch timestamp."""
    conn.execute("UPDATE punches SET ts = ? WHERE id = ?", (ts, punch_id))


def void_punch(conn: sqlite3.Connection, punch_id: int) -> None:
    """Mark one punch voided without deleting its audit history."""
    conn.execute("UPDATE punches SET voided = 1 WHERE id = ?", (punch_id,))


def update_schedule_end(conn: sqlite3.Connection, schedule_id: int, ts: str) -> None:
    """Update a schedule's planned end timestamp."""
    conn.execute("UPDATE schedules SET end_ts = ? WHERE id = ?", (ts, schedule_id))


def create_adjustment(
    conn: sqlite3.Connection,
    decision_id: int,
    exception_id: int,
    employee_id: str,
    adjustment_type: str,
    target_id: int | None,
    old_value: str | None,
    new_value: str | None,
    created_at: str,
) -> int:
    """Record one applied correction or approval annotation."""
    cursor = conn.execute(
        """INSERT INTO adjustments
           (decision_id, exception_id, employee_id, type, target_id,
            old_value, new_value, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            decision_id,
            exception_id,
            employee_id,
            adjustment_type,
            target_id,
            old_value,
            new_value,
            created_at,
        ),
    )
    return int(cursor.lastrowid)


def create_exception(
    conn: sqlite3.Connection,
    fingerprint: str,
    employee_id: str,
    work_date: date,
    rule_id: str,
    severity: Severity,
    evidence: dict[str, object],
    constraints: dict[str, object],
    detected_at: str,
) -> int:
    """Insert a newly detected exception in OPEN status."""
    cursor = conn.execute(
        """INSERT INTO exceptions
           (fingerprint, employee_id, work_date, rule_id, severity, status,
            evidence_json, constraints_json, detected_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fingerprint,
            employee_id,
            work_date.isoformat(),
            rule_id,
            severity.value,
            ExceptionStatus.OPEN.value,
            json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            json.dumps(constraints, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            detected_at,
        ),
    )
    return int(cursor.lastrowid)


def list_exceptions_for_employee(
    conn: sqlite3.Connection, employee_id: str
) -> list[sqlite3.Row]:
    """List persisted exceptions for one employee in primary-key order."""
    return conn.execute(
        "SELECT * FROM exceptions WHERE employee_id = ? ORDER BY id", (employee_id,)
    ).fetchall()


def set_exception_status(
    conn: sqlite3.Connection, exception_id: int, status: ExceptionStatus
) -> None:
    """Update an exception status by primary key."""
    conn.execute(
        "UPDATE exceptions SET status = ? WHERE id = ?", (status.value, exception_id)
    )
