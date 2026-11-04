import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./client";
import {
  auditEventSchema,
  auditVerifySchema,
  assistantAnswerSchema,
  decisionOutSchema,
  exceptionDetailSchema,
  exceptionOutSchema,
  healthSchema,
  llmModeSchema,
  metricsSchema,
  proposalOutSchema,
  type ExceptionStatus,
  type FaultMode,
} from "./schemas";
import { z } from "zod";

const API = "/api/v1";
const exceptionListSchema = z.array(exceptionOutSchema);
const detectOutSchema = z.object({
  as_of: z.string(),
  created: z.number(),
  unchanged: z.number(),
  superseded: z.number(),
});

export interface ExceptionFilters {
  status?: ExceptionStatus | "active" | "all";
  severity?: "low" | "medium" | "high";
  rule_id?: string;
}

const ACTIVE_STATUSES: ExceptionStatus[] = ["OPEN", "PENDING_REVIEW", "ESCALATED"];
const severityOrder = { high: 0, medium: 1, low: 2 };

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
        || a.id - b.id,
      );
    },
  });
}

export function useExceptionDetail(id: number | string | undefined) {
  const normalizedId = id === undefined ? undefined : Number(id);
  return useQuery({
    queryKey: ["exception", normalizedId],
    queryFn: () => apiFetch(`${API}/exceptions/${id}`, {}, exceptionDetailSchema),
    enabled: id !== undefined,
  });
}

export function usePropose(id: number) {
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

export function useDecide(id: number) {
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

export function useAudit(id: number, enabled = true) {
  return useQuery({
    queryKey: ["audit", id],
    queryFn: () => apiFetch(`${API}/exceptions/${id}/audit`, {}, z.array(auditEventSchema)),
    enabled,
  });
}

export function useMetrics() {
  return useQuery({ queryKey: ["metrics"], queryFn: () => apiFetch(`${API}/metrics`, {}, metricsSchema) });
}

export function useAssistantQuery() {
  return useMutation({
    mutationFn: (question: string) => apiFetch(`${API}/assistant/query`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }, assistantAnswerSchema),
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => apiFetch(`${API}/health`, {}, healthSchema),
    refetchInterval: 30_000,
  });
}

export function useLlmMode() {
  return useQuery({
    queryKey: ["llm-mode"],
    queryFn: () => apiFetch(`${API}/admin/llm-mode`, {}, llmModeSchema),
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
