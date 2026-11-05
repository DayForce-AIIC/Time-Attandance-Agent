import type { ExceptionOut, ProposalOut } from "../api/schemas";

export const exceptionSample: ExceptionOut = {
  id: 14,
  employee_id: "e-001",
  employee_name: "Priya Raman",
  work_date: "2025-03-12",
  rule_id: "PUNCH-01",
  title: "Missing out-punch",
  severity: "high",
  status: "PENDING_REVIEW",
  detected_at: "2025-03-13T20:00",
  evidence: { scheduled_end: "2025-03-12T17:00" },
  constraints: {
    allowed_actions: ["ADD_PUNCH", "ESCALATE"],
    ts_window: { earliest: "2025-03-12T12:31", latest: "2025-03-12T20:00" },
    target_kind: "OUT",
    target_punch_ids: [],
    target_schedule_id: null,
    flag_type: null,
    headroom_minutes: null,
  },
};

export const fallbackProposal: ProposalOut = {
  id: 4,
  exception_id: 14,
  trace_id: "trace-test-14",
  source: "fallback",
  proposal: {
    exception_id: 14,
    explanation: "Automated suggestion unavailable after validation failed. Manager review is required under PUNCH-01.",
    proposed_correction: {
      action: "ESCALATE",
      punch_id: null,
      kind: null,
      ts: null,
      schedule_id: null,
      note: null,
    },
    policy_ids_cited: ["PUNCH-01"],
    confidence: 0,
  },
  validation: {
    passed: true,
    checks: [{ id: "FB-01", name: "Fallback used", passed: true, level: "warning", detail: "Proposal was escalated." }],
    side_effects: [],
  },
  attempts: 2,
  model: "mock",
  latency_ms: 0,
  created_at: "2025-03-13T20:00",
  policy_snapshot: [],
};
