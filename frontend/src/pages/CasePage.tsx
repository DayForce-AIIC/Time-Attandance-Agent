import { Link, useParams } from "react-router-dom";
import { useDecide, useExceptionDetail, usePropose, useRetryWriteback, type DecisionBody } from "../api/hooks";
import { ErrorBanner, formatDate, formatHours, LoadingState, PageHeading, StatusBadge } from "../components/common";
import { AuditDrawer } from "../components/AuditDrawer";
import { DecisionPanel } from "../components/DecisionPanel";
import { PolicyPanel } from "../components/PolicyPanel";
import { ProposalCard } from "../components/ProposalCard";
import { SeverityBadge } from "../components/common";
import { TimecardTimeline } from "../components/TimecardTimeline";
import { useAuth } from "../auth/AuthContext";

export function CasePage() {
  const { id } = useParams();
  const auth = useAuth();
  const detail = useExceptionDetail(id);
  const proposalMutation = usePropose(id ?? "");
  const decisionMutation = useDecide(id ?? "");
  const retryWriteback = useRetryWriteback();

  if (detail.isLoading) return <main className="page-shell"><LoadingState label="Loading case evidence…" /></main>;
  if (detail.isError) {
    return <main className="page-shell"><PageHeading eyebrow="Case review" title="Case unavailable" /><ErrorBanner error={detail.error} onRetry={() => void detail.refetch()} /><Link className="button button-outline back-link" to="/">← Back to queue</Link></main>;
  }
  if (!detail.data) return null;

  const { exception, employee, timecard, policies, latest_proposal: proposal, decision } = detail.data;
  const canReview = detail.data.source_current
    && !decision
    && ["PENDING_REVIEW", "ESCALATED"].includes(exception.status);
  const sourceChangedBeforeDecision = !detail.data.source_current
    && !decision
    && ["OPEN", "PENDING_REVIEW", "ESCALATED"].includes(exception.status);
  const onDecision = (body: DecisionBody) => decisionMutation.mutateAsync(body);
  const approve = () => onDecision({ action: "approve" });
  const jumpToPolicy = (policyId: string) => {
    document.getElementById(`policy-${policyId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const thresholdMinutes = timecard.weekly_threshold_minutes ?? 0;
  const workedPercent = thresholdMinutes > 0
    ? Math.min(100, timecard.weekly_worked_minutes / thresholdMinutes * 100)
    : 0;

  return (
    <main className="page-shell case-page">
      <div className="case-topline">
        <Link to="/" className="back-link">← Queue</Link>
        <AuditDrawer exceptionId={exception.id} />
      </div>
      <PageHeading eyebrow={`${exception.rule_id} · ${exception.title}`} title={employee.name}>
        <div className="heading-badges"><SeverityBadge severity={exception.severity} /><StatusBadge status={exception.status} /></div>
      </PageHeading>

      {sourceChangedBeforeDecision ? (
        <div className="source-stale-banner" role="status">
          <strong>Attendance history changed after this case was analyzed.</strong>
          <span>Wait for the updated analysis before making a decision. Trigger record version: reviewed {detail.data.source_version ?? "unknown"}, current {detail.data.current_source_version ?? "record unavailable"}.</span>
        </div>
      ) : null}

      <div className="case-meta-grid">
        <MetaItem label="Role" value={employee.role} />
        <MetaItem label="Site" value={employee.site} />
        <MetaItem label="Jurisdiction" value={employee.jurisdiction} />
        <MetaItem label="Work date" value={formatDate(exception.work_date)} />
        <MetaItem label="Rule ID" value={exception.rule_id} mono />
        <MetaItem label="Employee ID" value={employee.id} mono />
        {detail.data.source_version ? <MetaItem label="Analyzed source version" value={detail.data.source_version} mono /> : null}
      </div>

      <div className="case-grid">
        <div className="case-evidence-column">
          <TimecardTimeline exception={exception} punches={timecard.punches} schedules={timecard.schedules} proposal={proposal} />

          <section className="panel punch-panel" aria-labelledby="punch-table-heading">
            <div className="panel-heading"><div><p className="eyebrow">Source records</p><h2 id="punch-table-heading">Punches · {exception.work_date}</h2></div><span className="count-chip">{timecard.punches.filter((punch) => punch.work_date === exception.work_date).length} records</span></div>
            <div className="table-scroll">
              <table className="data-table punch-table">
                <thead><tr><th scope="col">ID</th><th scope="col">Kind</th><th scope="col">Time</th><th scope="col">Source</th><th scope="col">State</th></tr></thead>
                <tbody>
                  {timecard.punches.filter((punch) => punch.work_date === exception.work_date).map((punch) => (
                    <tr className={punch.voided ? "punch-voided" : ""} key={punch.id}>
                      <td className="mono">#{punch.id}</td><td><span className={`punch-kind kind-${punch.kind.toLowerCase()}`}>{punch.kind.replaceAll("_", " ")}</span></td>
                      <td>{punch.ts.replace("T", " ")}</td><td>{punch.source}</td><td>{punch.voided ? <span className="voided-label">Voided</span> : "Active"}</td>
                    </tr>
                  ))}
                  {timecard.punches.every((punch) => punch.work_date !== exception.work_date) ? <tr><td colSpan={5}>No punch records for this date.</td></tr> : null}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel weekly-panel" aria-labelledby="weekly-heading">
            <div className="panel-heading"><div><p className="eyebrow">Monday–Sunday week · starts {timecard.week_start}</p><h2 id="weekly-heading">Weekly hours</h2></div><strong>{formatHours(timecard.weekly_worked_minutes)} <span className="muted-text">/ {timecard.weekly_threshold_minutes === null ? "No configured threshold" : formatHours(timecard.weekly_threshold_minutes)}</span></strong></div>
            <progress className="weekly-progress" max={100} value={workedPercent} aria-label={timecard.weekly_threshold_minutes === null ? "Weekly threshold is not configured" : `Weekly worked ${formatHours(timecard.weekly_worked_minutes)} of ${formatHours(timecard.weekly_threshold_minutes)} threshold`} />
            <div className="weekly-scale"><span>0 hours</span><span>{timecard.weekly_threshold_minutes === null ? "No configured threshold" : `Threshold ${formatHours(timecard.weekly_threshold_minutes)}`}</span></div>
          </section>

          <PolicyPanel policies={policies} proposal={proposal} />
        </div>

        <aside className="case-proposal-column" aria-label="Proposal and decision">
          {decision ? (
            <section className="panel decision-summary" aria-labelledby="decision-summary-heading">
              <p className="eyebrow">Decision recorded</p>
              <h2 id="decision-summary-heading">{decision.action === "reject" ? "Proposal rejected" : decision.writeback_status === "applied" ? "Correction applied" : "Correction approved"}</h2>
              <p>This case was decided by HR user <strong>{decision.manager_id}</strong> on {decision.decided_at.replace("T", " ")}.</p>
              <div className="decision-summary-grid"><MetaItem label="Action" value={decision.action} /><MetaItem label="Final status" value={decision.resulting_status} /><MetaItem label="Company writeback" value={decision.writeback_status} />{decision.external_operation_id ? <MetaItem label="Company operation" value={decision.external_operation_id} mono /> : null}</div>
              {decision.final_correction ? <pre className="decision-correction">{JSON.stringify(decision.final_correction, null, 2)}</pre> : null}
              {decision.note ? <blockquote>{decision.note}</blockquote> : null}
              {decision.writeback_status === "pending" || decision.writeback_status === "failed" ? (
                <>
                  <p className="muted-note">The HR decision is saved. Company writeback can be retried safely with the same operation key.</p>
                  {retryWriteback.isError ? <ErrorBanner error={retryWriteback.error} /> : null}
                  <button type="button" className="button button-primary" onClick={() => retryWriteback.mutate(String(decision.id))} disabled={retryWriteback.isPending}>
                    {retryWriteback.isPending ? "Retrying company writeback…" : "Retry company writeback"}
                  </button>
                </>
              ) : null}
              <Link className="button button-primary" to="/">Back to queue</Link>
            </section>
          ) : proposal ? (
            <>
              <ProposalCard exception={exception} proposal={proposal} onApprove={canReview ? approve : undefined} onPolicyClick={jumpToPolicy} />
              {canReview ? <DecisionPanel exception={exception} proposal={proposal} onDecision={onDecision} /> : null}
            </>
          ) : exception.status === "OPEN" ? (
            <section className="panel proposal-empty" aria-labelledby="proposal-empty-heading">
              <div className="proposal-empty-mark" aria-hidden="true">✦</div>
              {auth.enabled ? (
                <><p className="eyebrow">Automated analysis</p><h2 id="proposal-empty-heading">Analysis is queued</h2><p>The attendance event is being analyzed automatically. Refresh this case after the analysis worker finishes.</p></>
              ) : (
                <><p className="eyebrow">Next step</p><h2 id="proposal-empty-heading">Generate a manager-reviewed suggestion</h2><p>ShiftGuard will gather the relevant policy, build a bounded proposal, and validate it before showing any suggested timecard change.</p>
                  {proposalMutation.isError ? <ErrorBanner error={proposalMutation.error} onRetry={() => proposalMutation.mutate()} /> : null}
                  <button type="button" className="button button-primary button-full" onClick={() => proposalMutation.mutate()} disabled={proposalMutation.isPending}>
                    {proposalMutation.isPending ? <><span className="spinner spinner-light" aria-hidden="true" /> Generating and validating…</> : "✦ Generate AI proposal"}
                  </button></>
              )}
            </section>
          ) : (
            <section className="panel proposal-empty"><p className="eyebrow">No active suggestion</p><h2>Manager review required</h2><p>This case has no pending proposal. Re-run detection or return to the queue.</p><Link className="button button-outline" to="/">Back to queue</Link></section>
          )}
        </aside>
      </div>
    </main>
  );
}

function MetaItem({ label, value, mono = false }: { label: string; value: string | null | undefined; mono?: boolean }) {
  return <div className="meta-item"><span>{label}</span><strong className={mono ? "mono" : ""}>{value || "Not provided"}</strong></div>;
}
