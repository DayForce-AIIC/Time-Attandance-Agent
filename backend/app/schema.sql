PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS managers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL,
  site TEXT NOT NULL,
  jurisdiction TEXT NOT NULL CHECK (jurisdiction IN ('US-STD','US-CA')),
  manager_id TEXT NOT NULL REFERENCES managers(id),
  ot_eligible INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id TEXT NOT NULL REFERENCES employees(id),
  work_date TEXT NOT NULL,
  start_ts TEXT NOT NULL,
  end_ts TEXT NOT NULL,
  planned_break_minutes INTEGER NOT NULL DEFAULT 30,
  UNIQUE (employee_id, work_date)
);

CREATE TABLE IF NOT EXISTS punches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id TEXT NOT NULL REFERENCES employees(id),
  work_date TEXT NOT NULL,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('IN','OUT','BREAK_START','BREAK_END')),
  source TEXT NOT NULL CHECK (source IN ('device','manual','correction')),
  voided INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_punches_emp_date ON punches(employee_id, work_date);

CREATE TABLE IF NOT EXISTS employee_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id TEXT NOT NULL,
  work_date TEXT NOT NULL,
  text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exceptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT NOT NULL UNIQUE,
  employee_id TEXT NOT NULL REFERENCES employees(id),
  work_date TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  severity TEXT NOT NULL,
  status TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  constraints_json TEXT NOT NULL,
  detected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  exception_id INTEGER NOT NULL REFERENCES exceptions(id),
  trace_id TEXT NOT NULL,
  source TEXT NOT NULL,
  proposal_json TEXT NOT NULL,
  validation_json TEXT NOT NULL,
  attempts INTEGER NOT NULL,
  model TEXT NOT NULL,
  latency_ms INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  exception_id INTEGER NOT NULL REFERENCES exceptions(id),
  proposal_id INTEGER REFERENCES proposals(id),
  action TEXT NOT NULL,
  manager_id TEXT NOT NULL,
  note TEXT,
  final_correction_json TEXT,
  decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS adjustments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  exception_id INTEGER NOT NULL,
  employee_id TEXT NOT NULL,
  type TEXT NOT NULL CHECK (
    type IN ('ADD_PUNCH','MODIFY_PUNCH','VOID_PUNCH','ADJUST_SCHEDULE','ANNOTATION')
  ),
  target_id INTEGER,
  old_value TEXT,
  new_value TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id TEXT NOT NULL,
  exception_id INTEGER,
  seq INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;
