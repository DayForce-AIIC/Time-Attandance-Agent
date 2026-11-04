import { z } from "zod";

const timestamp = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)
  .refine((value) => !Number.isNaN(Date.parse(value)), "Invalid local timestamp");
const auditTimestamp = z.string().refine((value) => !Number.isNaN(Date.parse(value)), "Invalid audit timestamp");
const workflowIdSchema = z.union([z.number().int(), z.string().uuid()]);

export const severitySchema = z.enum(["low", "medium", "high", "critical"]);
export const exceptionStatusSchema = z.enum([
  "OPEN",
  "PENDING_REVIEW",
  "ESCALATED",
  "RESOLVED",
  "REJECTED",
  "SUPERSEDED",
  "WRITEBACK_PENDING",
  "WRITEBACK_FAILED",
]);
export const actionSchema = z.enum([
  "ADD_PUNCH",
  "MODIFY_PUNCH",
  "REMOVE_PUNCH",
  "ADJUST_SCHEDULE",
  "FLAG_FOR_APPROVAL",
  "ESCALATE",
]);
export const punchKindSchema = z.enum(["IN", "OUT", "BREAK_START", "BREAK_END"]);
export const faultModeSchema = z.enum([
  "none",
  "bad_then_good",
  "always_bad",
  "garbage",
  "timeout",
  "bad_citation",
  "naive_latest",
]);
export const decisionActionSchema = z.enum(["approve", "edit", "reject"]);

export const checkResultSchema = z.object({
  id: z.string(),
  name: z.string(),
  passed: z.boolean(),
  level: z.enum(["error", "warning"]).default("error"),
  detail: z.string().default(""),
});

export const sideEffectSchema = z.object({
  rule_id: z.string(),
  severity: severitySchema,
  work_date: z.string(),
  summary: z.string(),
});

export const validationResultSchema = z.object({
  passed: z.boolean(),
  checks: z.array(checkResultSchema),
  side_effects: z.array(sideEffectSchema).default([]),
});

export const timeWindowSchema = z.object({ earliest: timestamp, latest: timestamp });
export const constraintsSchema = z.object({
  allowed_actions: z.array(actionSchema),
  flag_type: z.string().nullable().optional(),
  ts_window: timeWindowSchema.nullable().optional(),
  target_kind: punchKindSchema.nullable().optional(),
  target_punch_ids: z.array(z.number()).default([]),
  target_schedule_id: z.number().nullable().optional(),
  headroom_minutes: z.number().nullable().optional(),
});

export const exceptionOutSchema = z.object({
  id: workflowIdSchema,
  employee_id: z.string(),
  employee_name: z.string(),
  work_date: z.string(),
  rule_id: z.string(),
  title: z.string(),
  severity: severitySchema,
  status: exceptionStatusSchema,
  detected_at: timestamp,
  evidence: z.record(z.string(), z.unknown()),
  constraints: constraintsSchema,
});

export const proposedCorrectionSchema = z
  .object({
    action: actionSchema,
    punch_id: z.number().int().nullable().optional(),
    kind: punchKindSchema.nullable().optional(),
    ts: timestamp.nullable().optional(),
    schedule_id: z.number().int().nullable().optional(),
    note: z.string().max(300).nullable().optional(),
  })
  .strict()
  .superRefine((correction, context) => {
    const needs: Partial<Record<(typeof correction)['action'], string[]>> = {
      ADD_PUNCH: ["kind", "ts"],
      MODIFY_PUNCH: ["punch_id", "ts"],
      REMOVE_PUNCH: ["punch_id"],
      ADJUST_SCHEDULE: ["schedule_id", "ts"],
    };
    const required = needs[correction.action] ?? [];
    for (const field of required) {
      if (correction[field as keyof typeof correction] == null) {
        context.addIssue({ code: "custom", path: [field], message: `${correction.action} requires ${field}` });
      }
    }
    const actionFields = ["punch_id", "kind", "ts", "schedule_id"] as const;
    for (const field of actionFields) {
      if (!required.includes(field) && correction[field] != null) {
        context.addIssue({ code: "custom", path: [field], message: `${correction.action} must not set ${field}` });
      }
    }
  });

export const llmProposalSchema = z.object({
  exception_id: z.number().int(),
  explanation: z.string().min(20).max(600),
  proposed_correction: proposedCorrectionSchema,
  policy_ids_cited: z.array(z.string()).min(1).max(5),
  confidence: z.number().min(0).max(1),
});

export const proposalOutSchema = z.object({
  id: workflowIdSchema,
  exception_id: workflowIdSchema,
  trace_id: z.string(),
  source: z.enum(["llm", "fallback"]),
  proposal: llmProposalSchema,
  validation: validationResultSchema,
  attempts: z.number().int(),
  model: z.string(),
  latency_ms: z.number(),
  created_at: timestamp,
});

export const employeeSchema = z.object({
  id: z.string(),
  name: z.string(),
  role: z.string().nullable().optional(),
  site: z.string().nullable().optional(),
  jurisdiction: z.string(),
  manager_id: z.string().nullable().optional(),
  ot_eligible: z.boolean().nullable().optional(),
});

export const punchSchema = z.object({
  id: z.number().int(),
  employee_id: z.string(),
  work_date: z.string(),
  ts: timestamp,
  kind: punchKindSchema,
  source: z.string().nullable(),
  voided: z.boolean(),
});

export const scheduleSchema = z.object({
  id: z.number().int(),
  employee_id: z.string(),
  work_date: z.string(),
  start: timestamp,
  end: timestamp,
  planned_break_minutes: z.number(),
});

export const timecardSchema = z.object({
  employee: employeeSchema,
  week_start: z.string(),
  punches: z.array(punchSchema),
  schedules: z.array(scheduleSchema),
  weekly_worked_minutes: z.number(),
  weekly_threshold_minutes: z.number().nullable(),
});

export const policyPassageSchema = z.object({
  policy_id: z.string(),
  title: z.string(),
  text: z.string(),
});

export const assistantSourceSchema = z.object({
  source_id: z.string(),
  kind: z.enum(["case", "policy"]),
  label: z.string(),
  excerpt: z.string(),
  href: z.string().nullable().optional(),
});

export const assistantAnswerSchema = z.object({
  answer: z.string(),
  sources: z.array(assistantSourceSchema),
  model: z.string(),
  used_fallback: z.boolean(),
  conversation_id: z.string().uuid().nullable(),
});

export const conversationSummarySchema = z.object({
  id: z.string().uuid(),
  title: z.string().nullable(),
  created_at: auditTimestamp,
  updated_at: auditTimestamp,
  last_message_preview: z.string().nullable(),
});

export const conversationMessageSchema = z.object({
  id: z.string().uuid(),
  role: z.enum(["user", "assistant"]),
  content: z.string(),
  model: z.string().nullable(),
  source_references: z.array(assistantSourceSchema),
  created_at: auditTimestamp,
});

export const conversationDetailSchema = z.object({
  id: z.string().uuid(),
  title: z.string().nullable(),
  created_at: auditTimestamp,
  updated_at: auditTimestamp,
  messages: z.array(conversationMessageSchema),
});

export const conversationDeleteSchema = z.object({ deleted: z.boolean() });

export const decisionOutSchema = z.object({
  id: workflowIdSchema,
  exception_id: workflowIdSchema,
  proposal_id: workflowIdSchema.nullable(),
  action: decisionActionSchema,
  manager_id: z.string(),
  note: z.string().nullable(),
  final_correction: proposedCorrectionSchema.nullable(),
  decided_at: timestamp,
  resulting_status: exceptionStatusSchema,
  writeback_status: z.enum(["not_requested", "pending", "applied", "stale", "failed"]),
  external_operation_id: z.string().nullable(),
});

export const exceptionDetailSchema = z.object({
  exception: exceptionOutSchema,
  employee: employeeSchema,
  timecard: timecardSchema,
  policies: z.array(policyPassageSchema),
  latest_proposal: proposalOutSchema.nullable(),
  decision: decisionOutSchema.nullable(),
  source_current: z.boolean(),
  source_version: z.string().nullable(),
  current_source_version: z.string().nullable(),
});

export const metricsSchema = z.object({
  total_exceptions: z.number(),
  by_status: z.record(z.string(), z.number()),
  by_rule: z.record(z.string(), z.number()),
  by_severity: z.record(z.string(), z.number()),
  proposals_total: z.number(),
  first_pass_validation_rate: z.number(),
  final_pass_rate: z.number(),
  escalation_rate: z.number(),
  approved_total: z.number(),
  approved_without_edit_rate: z.number(),
  median_seconds_to_decision: z.number().nullable(),
  estimated_minutes_saved: z.number(),
  assumptions: z.record(z.string(), z.number()),
  unsafe_proposals_reaching_manager: z.number(),
});

export const auditEventSchema = z.object({
  id: z.number().int(),
  trace_id: z.string(),
  exception_id: workflowIdSchema.nullable(),
  seq: z.number().int(),
  event_type: z.string(),
  payload: z.record(z.string(), z.unknown()),
  prev_hash: z.string(),
  hash: z.string(),
  created_at: auditTimestamp,
});

export const auditVerifySchema = z.object({
  ok: z.boolean(),
  events_checked: z.number(),
  first_bad_event_id: z.number().nullable(),
});

export const healthSchema = z.object({
  status: z.literal("ok"),
  provider: z.string(),
  model: z.string(),
  llm_reachable: z.boolean(),
  now: timestamp,
});

export const currentUserSchema = z.object({
  user_id: z.string(),
  organization_id: z.string().nullable(),
  role: z.enum(["HR", "manager", "integration_service"]),
  manager_ref: z.string().nullable(),
  email: z.string().nullable(),
});

export const llmModeSchema = z.object({ mode: faultModeSchema, provider: z.string(), model: z.string() });
export const errorOutSchema = z.object({
  detail: z.string(),
  code: z.string(),
  checks: z.array(checkResultSchema).nullable().optional(),
});

export type Severity = z.infer<typeof severitySchema>;
export type ExceptionStatus = z.infer<typeof exceptionStatusSchema>;
export type Action = z.infer<typeof actionSchema>;
export type PunchKind = z.infer<typeof punchKindSchema>;
export type CheckResult = z.infer<typeof checkResultSchema>;
export type ExceptionOut = z.infer<typeof exceptionOutSchema>;
export type ProposedCorrection = z.infer<typeof proposedCorrectionSchema>;
export type ProposalOut = z.infer<typeof proposalOutSchema>;
export type ExceptionDetail = z.infer<typeof exceptionDetailSchema>;
export type Metrics = z.infer<typeof metricsSchema>;
export type AuditEvent = z.infer<typeof auditEventSchema>;
export type DecisionOut = z.infer<typeof decisionOutSchema>;
export type FaultMode = z.infer<typeof faultModeSchema>;
