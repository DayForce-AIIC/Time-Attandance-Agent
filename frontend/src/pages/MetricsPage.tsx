import { useMetrics, useVerifyAudit } from "../api/hooks";
import { ErrorBanner, formatHours, LoadingState, PageHeading } from "../components/common";

export function MetricsPage() {
  const metrics = useMetrics();
  const audit = useVerifyAudit(true);

  if (metrics.isLoading) return <main className="page-shell"><LoadingState label="Loading workflow metrics…" /></main>;
  if (metrics.isError) return <main className="page-shell"><PageHeading eyebrow="Operations" title="Workflow metrics" /><ErrorBanner error={metrics.error} onRetry={() => void metrics.refetch()} /></main>;
  if (!metrics.data) return null;
  const data = metrics.data;
  const ruleEntries = Object.entries(data.by_rule).sort((a, b) => b[1] - a[1]);
  const maxRuleCount = Math.max(1, ...ruleEntries.map(([, count]) => count));
  const cards = [
    { label: "Total exceptions", value: String(data.total_exceptions), note: "Across the current seeded review data", tone: "" },
    { label: "Open for review", value: String(data.by_status.OPEN ?? 0), note: "Not yet submitted for a proposal", tone: "" },
    { label: "Resolved", value: String(data.by_status.RESOLVED ?? 0), note: "Manager decision recorded", tone: "" },
    { label: "Escalation rate", value: percent(data.escalation_rate), note: `${data.proposals_total} proposals generated`, tone: "" },
    { label: "First-pass validation", value: percent(data.first_pass_validation_rate), note: "Passed without a retry", tone: "" },
    { label: "Approved without edit", value: percent(data.approved_without_edit_rate), note: `${data.approved_total} approve/edit decisions`, tone: "" },
    { label: "Median time to decision", value: data.median_seconds_to_decision === null ? "—" : `${Math.round(data.median_seconds_to_decision)} sec`, note: "From proposal creation to manager response", tone: "" },
    { label: "Estimated time saved", value: `${formatHours(data.estimated_minutes_saved)}*`, note: "Based on completed approvals", tone: "" },
    { label: "Unsafe proposals reaching manager", value: String(data.unsafe_proposals_reaching_manager), note: data.unsafe_proposals_reaching_manager === 0 ? "No unsafe proposal is waiting for review" : "Requires immediate review", tone: data.unsafe_proposals_reaching_manager === 0 ? "metric-safe" : "metric-risk" },
  ];

  return (
    <main className="page-shell metrics-page">
      <PageHeading eyebrow="Operations overview" title="Workflow metrics" subtitle="Operational efficiency, decision telemetry, and audit-chain integrity.">
        <span className="updated-pill"><span className="health-dot" aria-hidden="true" /> Live from API</span>
      </PageHeading>
      <section className="metric-grid" aria-label="Key workflow metrics">
        {cards.map((card) => <article className={`metric-card ${card.tone}`} key={card.label}><span>{card.label}</span><strong>{card.value}</strong><small>{card.note}</small></article>)}
      </section>
      <p className="assumptions-note">* Estimated minutes saved = {data.assumptions.manual_minutes_per_exception ?? 0} min manual review assumption minus {data.assumptions.review_minutes_per_exception ?? 0} min assisted review assumption, for each approved case.</p>

      <div className="metrics-lower-grid">
        <section className="panel rule-metrics" aria-labelledby="rule-metrics-heading">
          <div className="panel-heading"><div><p className="eyebrow">Where issues occur</p><h2 id="rule-metrics-heading">Exceptions by rule</h2></div></div>
          {ruleEntries.length ? <ul className="rule-bar-list">{ruleEntries.map(([rule, count]) => (
            <li key={rule}><div className="rule-bar-label"><span className="rule-code">{rule}</span><strong>{count}</strong></div><div className="rule-bar-track"><span style={{ width: `${count / maxRuleCount * 100}%` }} /></div></li>
          ))}</ul> : <p className="muted-note">No rule data yet.</p>}
        </section>
        <section className="panel audit-status-panel" aria-labelledby="audit-status-heading">
          <div className="panel-heading"><div><p className="eyebrow">Tamper evidence</p><h2 id="audit-status-heading">Audit chain</h2></div></div>
          {audit.isError ? <ErrorBanner error={audit.error} onRetry={() => void audit.refetch()} /> : audit.isLoading ? <LoadingState label="Verifying audit chain…" /> : audit.data ? (
            <div className={`audit-status-card ${audit.data.ok ? "audit-integrity-ok" : "audit-integrity-fail"}`}>
              <span className="audit-status-icon" aria-hidden="true">{audit.data.ok ? "✓" : "✕"}</span>
              <div><strong>{audit.data.ok ? "Chain intact" : "Chain verification failed"}</strong><p>{audit.data.ok ? `${audit.data.events_checked} events checked` : `First mismatch at event ${audit.data.first_bad_event_id ?? "unknown"}`}</p></div>
            </div>
          ) : null}
          <button type="button" className="button button-outline" onClick={() => void audit.refetch()} disabled={audit.isFetching}>{audit.isFetching ? "Verifying…" : "↻ Verify again"}</button>
        </section>
      </div>
    </main>
  );
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}
