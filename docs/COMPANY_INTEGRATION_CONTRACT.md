# Company attendance integration contract

**Status:** Step 0 draft for agreement with the company that owns the HR/timekeeping system.

This contract keeps the company's HR/timekeeping system as the source of truth. ShiftGuard stores workflow state and references; it does not import the employee directory or attendance history.

## Integration shape

The company system provides a versioned HTTPS API and either:

1. Sends signed change webhooks to ShiftGuard, with cursor-based delta sync available to recover missed events; or
2. Exposes a cursor-based change feed that ShiftGuard polls.

The integration identity determines the organization. Do not trust an organization ID supplied in an event body. All timestamps include an explicit offset or use UTC, dates use `YYYY-MM-DD`, and employee/record IDs are opaque stable strings.

## 1. Notify ShiftGuard of a changed record

```http
POST /integrations/v1/events/attendance-changed
Content-Type: application/json
X-Company-Event-Id: evt_01JQ8A3B7C
X-Company-Timestamp: 2026-11-04T09:15:00Z
X-Company-Signature: sha256=<signature>
```

```json
{
  "event_id": "evt_01JQ8A3B7C",
  "event_type": "attendance.record.changed",
  "occurred_at": "2026-11-04T09:14:52Z",
  "record": {
    "employee_ref": "emp_8F39A",
    "record_type": "timecard_day",
    "record_ref": "tcday_2026-11-03_8F39A",
    "version": "17",
    "updated_at": "2026-11-04T09:14:52Z"
  }
}
```

ShiftGuard verifies the signature, records the event ID and source version, and responds quickly without waiting for the LLM:

```http
202 Accepted
```

```json
{
  "event_id": "evt_01JQ8A3B7C",
  "status": "accepted",
  "duplicate": false
}
```

The same event ID may be delivered more than once. A repeated event receives `202` with `duplicate: true` and must not create a second analysis job. A deleted source record uses `attendance.record.deleted`; ShiftGuard marks affected open work stale and retains its audit history.

## 2. Read the minimum evidence for analysis

The worker fetches current or version-pinned evidence by stable references. The company API should support a version token or `If-Match`/ETag so ShiftGuard can detect a record that changed after analysis.

```http
GET /v1/attendance/records/tcday_2026-11-03_8F39A?version=17&include=punches,schedule,leave
Authorization: Bearer <integration credential>
```

```json
{
  "record_ref": "tcday_2026-11-03_8F39A",
  "employee_ref": "emp_8F39A",
  "version": "17",
  "updated_at": "2026-11-04T09:14:52Z",
  "work_date": "2026-11-03",
  "timezone": "America/Chicago",
  "jurisdiction": "US-TX",
  "schedule": {
    "record_ref": "shift_7712",
    "start": "2026-11-03T08:00:00-06:00",
    "end": "2026-11-03T16:30:00-06:00",
    "planned_break_minutes": 30
  },
  "punches": [
    {"record_ref": "p_1001", "kind": "IN", "at": "2026-11-03T08:02:00-06:00", "source": "device"},
    {"record_ref": "p_1002", "kind": "BREAK_START", "at": "2026-11-03T12:00:00-06:00", "source": "device"},
    {"record_ref": "p_1003", "kind": "BREAK_END", "at": "2026-11-03T12:30:00-06:00", "source": "device"}
  ],
  "leave": []
}
```

Only fields needed to detect and explain the specific exception should be returned. The response must not include unrelated employees or attendance history. If historical versions cannot be fetched, include a stable `version` and `updated_at`; ShiftGuard will retain a minimized evidence snapshot and hash for the case.

## 3. Submit an HR-approved correction

ShiftGuard sends a correction only after an authorized HR user approves it. The request includes an idempotency key and the version reviewed by HR.

```http
POST /v1/attendance/corrections
Authorization: Bearer <integration credential>
Idempotency-Key: sg_decision_01JQ8B1M9N
If-Match: "17"
Content-Type: application/json
```

```json
{
  "decision_ref": "01JQ8B1M9N",
  "record_ref": "tcday_2026-11-03_8F39A",
  "employee_ref": "emp_8F39A",
  "expected_version": "17",
  "correction": {
    "action": "ADD_PUNCH",
    "at": "2026-11-03T16:30:00-06:00",
    "kind": "OUT"
  },
  "approved_by": "hr_user_204",
  "approved_at": "2026-11-04T10:02:11Z"
}
```

```http
202 Accepted
```

```json
{
  "operation_id": "corr_88AB1",
  "status": "applied",
  "record_ref": "tcday_2026-11-03_8F39A",
  "new_version": "18",
  "applied_at": "2026-11-04T10:02:12Z"
}
```

If the company system processes corrections asynchronously, it returns `status: "pending"` and exposes `GET /v1/attendance/corrections/{operation_id}`. Retrying the same idempotency key returns the original operation rather than applying the correction twice.

The company system must reject a stale `expected_version` with `409 Conflict`; ShiftGuard then marks the proposal stale and fetches fresh evidence. Validation failures use `422` with a machine-readable error code. `429` includes `Retry-After`; transient `5xx` responses may be retried with backoff. `401`/`403` are configuration failures and must be surfaced to an administrator.

## Decisions to confirm with the company

- Webhooks, cursor feed, or both; signature algorithm, key rotation, retry window, and delivery guarantees.
- Stable employee, timecard-day, punch, schedule, and leave identifiers and version semantics.
- Which records and fields may be read for a case, and how deleted/voided records appear.
- Authoritative policies, jurisdiction identifiers, work-time zone, leave representation, and whether corrections can be submitted as applied changes or only recommendations.
- Whether HR approval is represented by the integration identity, a company user reference, or both.
- Rate limits, retention requirements, and handling of outages or unavailable historical versions.

## Contract exit checklist

- [ ] Company confirms the webhook or cursor-feed example and retry/idempotency behavior.
- [ ] Company confirms the evidence-read fields, stable references, and version/concurrency token.
- [ ] Company confirms the correction payload, approval semantics, and writeback operation lifecycle.
- [ ] Company confirms policy authority, time zones, and data-retention constraints.
