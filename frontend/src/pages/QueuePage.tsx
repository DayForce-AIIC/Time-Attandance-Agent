import { useMemo, useState } from "react";
import { useExceptions } from "../api/hooks";
import type { ExceptionStatus } from "../api/schemas";
import { AssistantLauncher } from "../components/AssistantLauncher";
import { ErrorBanner, LoadingState, PageHeading } from "../components/common";
import { ExceptionTable } from "../components/ExceptionTable";
import { useAuth } from "../auth/AuthContext";

const STATUSES: { value: ExceptionStatus; label: string }[] = [
  { value: "OPEN", label: "Open" },
  { value: "PENDING_REVIEW", label: "Pending review" },
  { value: "ESCALATED", label: "Escalated" },
  { value: "RESOLVED", label: "Resolved" },
  { value: "REJECTED", label: "Rejected" },
  { value: "SUPERSEDED", label: "Superseded" },
  { value: "WRITEBACK_PENDING", label: "Writeback pending" },
  { value: "WRITEBACK_FAILED", label: "Writeback failed" },
];

const RULES = [
  "PUNCH-01", "PUNCH-02", "PUNCH-03", "PUNCH-04", "MAX-01", "BRK-01", "BRK-02",
  "OT-01", "OT-02", "OT-03", "SCHED-01", "SCHED-02", "REST-01", "DAY-01",
];

const SUMMARY: { key: ExceptionStatus; label: string; tone: "neutral" | "info" | "warning" | "danger" | "success" }[] = [
  { key: "OPEN", label: "Open", tone: "neutral" },
  { key: "PENDING_REVIEW", label: "Pending review", tone: "info" },
  { key: "ESCALATED", label: "Escalated", tone: "warning" },
  { key: "WRITEBACK_PENDING", label: "Writeback pending", tone: "neutral" },
  { key: "WRITEBACK_FAILED", label: "Writeback failed", tone: "danger" },
  { key: "RESOLVED", label: "Resolved", tone: "success" },
];

export function QueuePage() {
  const auth = useAuth();
  const [status, setStatus] = useState<ExceptionStatus | "active" | "all">("active");
  const [severity, setSeverity] = useState<"" | "low" | "medium" | "high" | "critical">("");
  const [rule, setRule] = useState("");
  const query = useExceptions({ status, severity: severity || undefined, rule_id: rule || undefined });
  const allCases = useExceptions({ status: "all" });
  const counts = useMemo(() => {
    const totals: Record<string, number> = {};
    for (const item of allCases.data ?? []) totals[item.status] = (totals[item.status] ?? 0) + 1;
    return totals;
  }, [allCases.data]);

  return (
    <main className="page-shell">
      <PageHeading
        eyebrow={auth.enabled ? "HR review queue" : "Manager queue"}
        title="Timecard exceptions"
        subtitle="Review attendance anomalies, inspect the evidence, and decide on validated corrections."
      >
        <span className="queue-date">Pay week · Mar 10–16, 2025</span>
        <span className="queue-total"><strong>{allCases.data?.length ?? "—"}</strong> total cases</span>
      </PageHeading>

      <section className="summary-strip" aria-label="Exception counts by status">
        {SUMMARY.map(({ key, label, tone }) => {
          const count = counts[key] ?? (allCases.isLoading ? null : 0);
          return (
            <div className={`summary-item tone-${tone}${count === 0 ? " is-zero" : ""}`} key={key}>
              <span>{label}</span><strong>{count ?? "—"}</strong>
            </div>
          );
        })}
      </section>

      <section className="panel queue-panel" aria-labelledby="queue-heading">
        <div className="panel-heading queue-panel-heading">
          <div>
            <p className="eyebrow">Review work</p>
            <h2 id="queue-heading">Exception queue</h2>
          </div>
          <div className="filter-row">
            <label>
              <span>Status</span>
              <select aria-label="Filter by status" value={status} onChange={(event) => setStatus(event.target.value as typeof status)}>
                <option value="active">Active cases</option>
                <option value="all">All statuses</option>
                {STATUSES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            <label>
              <span>Severity</span>
              <select aria-label="Filter by severity" value={severity} onChange={(event) => setSeverity(event.target.value as typeof severity)}>
                <option value="">All levels</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>
            </label>
            <label>
              <span>Rule</span>
              <select aria-label="Filter by rule" value={rule} onChange={(event) => setRule(event.target.value)}>
                <option value="">All rules</option>
                {RULES.map((ruleId) => <option key={ruleId}>{ruleId}</option>)}
              </select>
            </label>
          </div>
        </div>

        {query.isError ? <ErrorBanner error={query.error} onRetry={() => void query.refetch()} /> : null}
        {query.isLoading ? <LoadingState label="Loading exception queue…" /> : <ExceptionTable exceptions={query.data ?? []} />}
        <div className="table-footer">Showing {query.data?.length ?? 0} cases · Sorted by severity and work date</div>
      </section>

      <AssistantLauncher />
    </main>
  );
}
