import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, apiStream, type ApiStreamEvent } from "./client";
import {
  auditEventSchema,
  auditVerifySchema,
  assistantAnswerSchema,
  conversationDeleteSchema,
  conversationDetailSchema,
  conversationSummarySchema,
  currentUserSchema,
  decisionOutSchema,
  exceptionDetailSchema,
  exceptionOutSchema,
  healthSchema,
  llmModeSchema,
  metricsSchema,
  organizationPolicyListSchema,
  organizationPolicySchema,
  proposalOutSchema,
  type OrganizationPolicyCreate,
  type OrganizationPolicyUpdate,
  type ExceptionStatus,
  type FaultMode,
} from "./schemas";
import { z } from "zod";

const API = "/api/v1";
const exceptionListSchema = z.array(exceptionOutSchema);
const conversationListSchema = z.array(conversationSummarySchema);
const detectOutSchema = z.object({
  as_of: z.string(),
  created: z.number(),
  unchanged: z.number(),
  superseded: z.number(),
});

export interface ExceptionFilters {
  status?: ExceptionStatus | "active" | "all";
  severity?: "low" | "medium" | "high" | "critical";
  rule_id?: string;
}

const ACTIVE_STATUSES: ExceptionStatus[] = [
  "OPEN",
  "PENDING_REVIEW",
  "ESCALATED",
  "WRITEBACK_PENDING",
  "WRITEBACK_FAILED",
];
const severityOrder = { critical: 0, high: 1, medium: 2, low: 3 };
export type WorkflowId = number | string;

export function useExceptions(filters: ExceptionFilters = {}) {
  return useQuery({
    queryKey: ["exceptions", filters],
    queryFn: async () => {
      const statuses = filters.status === "active" ? ACTIVE_STATUSES : [filters.status];
      const list = await Promise.all(
        statuses.map((status) => {
          const params = new URLSearchParams({ limit: "500" });
          if (status && status !== "all") params.set("status", status);
          if (filters.severity) params.set("severity", filters.severity);
          if (filters.rule_id) params.set("rule_id", filters.rule_id);
          return apiFetch(`${API}/exceptions?${params.toString()}`, {}, exceptionListSchema);
        }),
      );
      return list.flat().sort((a, b) =>
        severityOrder[a.severity] - severityOrder[b.severity]
        || a.work_date.localeCompare(b.work_date)
        || String(a.id).localeCompare(String(b.id)),
      );
    },
  });
}

export function useExceptionDetail(id: number | string | undefined) {
  return useQuery({
    queryKey: ["exception", id],
    queryFn: () => apiFetch(`${API}/exceptions/${id}`, {}, exceptionDetailSchema),
    enabled: id !== undefined,
  });
}

export function usePropose(id: WorkflowId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch(`${API}/exceptions/${id}/propose`, { method: "POST" }, proposalOutSchema),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["exception", id] }),
        queryClient.invalidateQueries({ queryKey: ["exceptions"] }),
        queryClient.invalidateQueries({ queryKey: ["metrics"] }),
      ]);
    },
  });
}

export interface DecisionBody {
  action: "approve" | "edit" | "reject";
  edited_correction?: import("./schemas").ProposedCorrection;
  note?: string;
}

export function useDecide(id: WorkflowId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: DecisionBody) => apiFetch(`${API}/exceptions/${id}/decision`, {
      method: "POST",
      body: JSON.stringify(body),
    }, decisionOutSchema),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["exception", id] }),
        queryClient.invalidateQueries({ queryKey: ["exceptions"] }),
        queryClient.invalidateQueries({ queryKey: ["metrics"] }),
      ]);
    },
  });
}

export function useAudit(id: WorkflowId, enabled = true) {
  return useQuery({
    queryKey: ["audit", id],
    queryFn: () => apiFetch(`${API}/exceptions/${id}/audit`, {}, z.array(auditEventSchema)),
    enabled,
  });
}

export function useRetryWriteback() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (decisionId: string) => apiFetch(
      `${API}/decisions/${decisionId}/retry-writeback`,
      { method: "POST" },
      decisionOutSchema,
    ),
    onSuccess: async (decision) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["exception", decision.exception_id] }),
        queryClient.invalidateQueries({ queryKey: ["exceptions"] }),
      ]);
    },
  });
}

export function useMetrics() {
  return useQuery({ queryKey: ["metrics"], queryFn: () => apiFetch(`${API}/metrics`, {}, metricsSchema) });
}

export function useAssistantQuery() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      question: string;
      conversation_id: string | null;
      onEvent?: (event: ApiStreamEvent) => void;
    }) => apiStream(`${API}/assistant/query/stream`, {
      method: "POST",
      body: JSON.stringify({ question: input.question, conversation_id: input.conversation_id }),
    }, assistantAnswerSchema, (event) => {
      if (event.event === "conversation") {
        void queryClient.invalidateQueries({ queryKey: ["assistant-conversations"] });
      }
      input.onEvent?.(event);
    }),
    onSuccess: async (answer) => {
      if (answer.conversation_id) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["assistant-conversations"] }),
          queryClient.invalidateQueries({ queryKey: ["assistant-conversation", answer.conversation_id] }),
        ]);
      }
    },
  });
}

export function useAssistantConversations(enabled: boolean) {
  return useQuery({
    queryKey: ["assistant-conversations"],
    queryFn: () => apiFetch(`${API}/assistant/conversations`, {}, conversationListSchema),
    enabled,
  });
}

export function useAssistantConversation(id: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["assistant-conversation", id],
    queryFn: () => apiFetch(`${API}/assistant/conversations/${id}`, {}, conversationDetailSchema),
    enabled: enabled && Boolean(id),
  });
}

export function useDeleteAssistantConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetch(`${API}/assistant/conversations/${id}`, {
      method: "DELETE",
    }, conversationDeleteSchema),
    onSuccess: async (_result, id) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["assistant-conversations"] }),
        queryClient.removeQueries({ queryKey: ["assistant-conversation", id] }),
      ]);
    },
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => apiFetch(`${API}/health`, {}, healthSchema),
    refetchInterval: 30_000,
  });
}

export function useCurrentUser(enabled = true) {
  return useQuery({
    queryKey: ["current-user"],
    queryFn: () => apiFetch(`${API}/me`, {}, currentUserSchema),
    enabled,
    retry: false,
    staleTime: 60_000,
  });
}

export function useLlmMode(enabled = true) {
  return useQuery({
    queryKey: ["llm-mode"],
    queryFn: () => apiFetch(`${API}/admin/llm-mode`, {}, llmModeSchema),
    enabled,
    retry: false,
    staleTime: 5_000,
  });
}

export function useSetLlmMode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (mode: FaultMode) => apiFetch(`${API}/admin/llm-mode`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }, llmModeSchema),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["llm-mode"] }),
  });
}

export function useDetect() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch(`${API}/detect`, {
      method: "POST",
      body: JSON.stringify({}),
    }, detectOutSchema),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["exceptions"] }),
  });
}

export function useResetDemo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch(`${API}/admin/reset-demo`, {
      method: "POST",
    }, detectOutSchema),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["exceptions"] }),
        queryClient.invalidateQueries({ queryKey: ["llm-mode"] }),
        queryClient.invalidateQueries({ queryKey: ["metrics"] }),
      ]);
    },
  });
}

export function useVerifyAudit(enabled: boolean) {
  return useQuery({
    queryKey: ["audit-verify"],
    queryFn: () => apiFetch(`${API}/audit/verify`, {}, auditVerifySchema),
    enabled,
  });
}

export function useOrganizationPolicies(enabled = true) {
  return useQuery({
    queryKey: ["organization-policies"],
    queryFn: () => apiFetch(`${API}/organization/policies`, {}, organizationPolicyListSchema),
    enabled,
  });
}

function useRefreshOrganizationPolicies() {
  const queryClient = useQueryClient();
  return async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["organization-policies"] }),
      queryClient.invalidateQueries({ queryKey: ["exceptions"] }),
    ]);
  };
}

export function useCreateOrganizationPolicy() {
  const refresh = useRefreshOrganizationPolicies();
  return useMutation({
    mutationFn: (body: OrganizationPolicyCreate) => apiFetch(
      `${API}/organization/policies`,
      { method: "POST", body: JSON.stringify(body) },
      organizationPolicySchema,
    ),
    onSuccess: refresh,
  });
}

export function useUpdateOrganizationPolicy() {
  const refresh = useRefreshOrganizationPolicies();
  return useMutation({
    mutationFn: ({ policyId, body }: { policyId: string; body: OrganizationPolicyUpdate }) => apiFetch(
      `${API}/organization/policies/${encodeURIComponent(policyId)}`,
      { method: "PATCH", body: JSON.stringify(body) },
      organizationPolicySchema,
    ),
    onSuccess: refresh,
  });
}

export function useArchiveOrganizationPolicy() {
  const refresh = useRefreshOrganizationPolicies();
  return useMutation({
    mutationFn: (policyId: string) => apiFetch(
      `${API}/organization/policies/${encodeURIComponent(policyId)}`,
      { method: "DELETE" },
      organizationPolicySchema,
    ),
    onSuccess: refresh,
  });
}
