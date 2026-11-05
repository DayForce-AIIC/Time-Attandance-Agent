import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useCurrentUser, useDecide, useExceptionDetail, usePropose, useRetryWriteback, type DecisionBody } from "../api/hooks";
import { Avatar, ErrorBanner, formatDate, formatHours, LoadingState, PageHeading, SeverityBadge, StatusBadge } from "../components/common";
import { AuditDrawer } from "../components/AuditDrawer";
import { DecisionPanel } from "../components/DecisionPanel";
import { DecisionSummary } from "../components/DecisionSummary";
import { EvidencePanel } from "../components/EvidencePanel";
import { PolicyPanel } from "../components/PolicyPanel";
import { ProposalCard } from "../components/ProposalCard";
import { useAuth } from "../auth/AuthContext";
import { BRAND } from "../brand";
import { splitPolicyText } from "../policyText";

export function CasePage() {
  const { id } = useParams();
  const auth = useAuth();
  const currentUser = useCurrentUser(auth.enabled);
  const detail = useExceptionDetail(id);
  const proposalMutation = usePropose(id ?? "");
  const decisionMutation = useDecide(id ?? "");
  const retryWriteback = useRetryWriteback();
  const [policiesOpen, setPoliciesOpen] = useState(false);

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
    setPoliciesOpen(true);
    window.setTimeout(() => {
      document.getElementById(`policy-${policyId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 80);
  };
  const thresholdMinutes = timecard.weekly_threshold_minutes ?? 0;
  const workedPercent = thresholdMinutes > 0
    ? Math.min(100, timecard.weekly_worked_minutes / thresholdMinutes * 100)
    : 0;
  const hoursUnavailable = timecard.weekly_worked_minutes === 0 && timecard.punches.some((punch) => !punch.voided);
  const showWeekly = timecard.weekly_worked_minutes > 0 || /^(OT|MAX|DAY|REST)-/.test(exception.rule_id);
  const policyTitles = Object.fromEntries(
    [...policies, ...(proposal?.policy_snapshot ?? [])].map((policy) => [policy.policy_id, policy.title]),
  );
  const rulePolicyText = policies.find((policy) => policy.policy_id === exception.rule_id)?.text;
  const rulePolicy = rulePolicyText ? splitPolicyText(rulePolicyText) : null;
  const caseRef = String(exception.id).length > 12 ? String(exception.id).slice(0, 8).toUpperCase() : String(exception.id);
  const employeeLine = [employee.role, employee.site].filter(Boolean).join(" · ") || `Employee ID ${employee.id}`;

  return (
    <main className="page-shell case-page">
      <div className="case-topline">
        <Link to="/" className="button button-outline">← Back to queue</Link>
        <AuditDrawer exceptionId={exception.id} />
      </div>

      <header className="panel case-header">
        <div className="case-identity">
          <Avatar name={employee.name} />
          <div>
            <p className="eyebrow">Case #{caseRef} · {formatDate(exception.work_date)}</p>
            <h1>{employee.name}</h1>
            <p className="case-subline">{employeeLine}</p>
          </div>
        </div>
        <div className="heading-badges"><SeverityBadge severity={exception.severity} /><StatusBadge status={exception.status} /></div>
      </header>

      {sourceChangedBeforeDecision ? (
        <div className="source-stale-banner" role="status">
          <strong>Attendance history changed after this case was analyzed.</strong>
          <span>Wait for the updated analysis before making a decision.</span>
        </div>
      ) : null}

      <div className="case-grid">
        <div className="case-evidence-column">
          <section className="panel case-summary" aria-labelledby="case-summary-heading">
            <p className="eyebrow">Why this was flagged</p>
            <h2 id="case-summary-heading">{exception.title}</h2>
            {rulePolicy ? <p className="case-summary-text">{rulePolicy.body}</p> : null}
            {rulePolicy?.source ? <p className="case-summary-source">Source: {rulePolicy.source}</p> : null}
            <div className="case-facts">
              <MetaItem label="Work date" value={formatDate(exception.work_date)} />
              <MetaItem label="Rule" value={exception.rule_id} mono />
              {employee.role ? <MetaItem label="Role" value={employee.role} /> : null}
              {employee.site ? <MetaItem label="Site" value={employee.site} /> : null}
              <MetaItem label="Employee ID" value={employee.id} mono />
            </div>
          </section>

          <EvidencePanel exception={exception} punches={timecard.punches} schedules={timecard.schedules} proposal={proposal} />
        </div>

        <aside className="case-proposal-column" aria-label="Recommendation and decision">
          {decision ? (
            <DecisionSummary
              decision={decision}
              currentUserId={currentUser.data?.user_id}
              utc={auth.enabled}
              onRetryWriteback={() => retryWriteback.mutate(String(decision.id))}
              retrying={retryWriteback.isPending}
              retryError={retryWriteback.isError ? retryWriteback.error : null}
            />
          ) : proposal ? (
            <div className="decision-stack">
              <ProposalCard exception={exception} proposal={proposal} showActions={false} onPolicyClick={jumpToPolicy} policyTitles={policyTitles} />
              {canReview ? <DecisionPanel exception={exception} proposal={proposal} onDecision={onDecision} onApprove={approve} /> : null}
            </div>
          ) : exception.status === "OPEN" ? (
            <section className="panel proposal-empty" aria-labelledby="proposal-empty-heading">
              <div className="proposal-empty-mark" aria-hidden="true">✦</div>
              {auth.enabled ? (
                <><p className="eyebrow">Automated analysis</p><h2 id="proposal-empty-heading">Analysis is queued</h2><p>The attendance event is being analyzed automatically. Refresh this case after the analysis worker finishes.</p></>
              ) : (
                <><p className="eyebrow">Next step</p><h2 id="proposal-empty-heading">Generate a manager-reviewed suggestion</h2><p>{BRAND.name} will gather the relevant policy, build a bounded proposal, and validate it before showing any suggested timecard change.</p>
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

        <div className="case-support-column">
          {showWeekly ? (
            <section className="panel weekly-panel" aria-labelledby="weekly-heading">
              <div className="panel-heading"><div><p className="eyebrow">Week of {formatDate(timecard.week_start)}</p><h2 id="weekly-heading">Weekly hours</h2></div><strong>{formatHours(timecard.weekly_worked_minutes)} <span className="muted-text">/ {timecard.weekly_threshold_minutes === null ? "no limit set" : formatHours(timecard.weekly_threshold_minutes)}</span></strong></div>
              <progress className="weekly-progress" max={100} value={workedPercent} aria-label={timecard.weekly_threshold_minutes === null ? "Weekly threshold is not configured" : `Weekly worked ${formatHours(timecard.weekly_worked_minutes)} of ${formatHours(timecard.weekly_threshold_minutes)} threshold`} />
              <div className="weekly-scale"><span>0 hours</span><span>{timecard.weekly_threshold_minutes === null ? "No configured threshold" : `Limit ${formatHours(timecard.weekly_threshold_minutes)}`}</span></div>
              {hoursUnavailable ? <p className="weekly-note">Hours can’t be counted yet: this week’s punches include a sequence the system can’t interpret, so the total shows 0h.</p> : null}
            </section>
          ) : null}

          <PolicyPanel policies={policies} proposal={proposal} open={policiesOpen} onToggle={setPoliciesOpen} />

          <details className="panel tech-details">
            <summary className="panel-heading">
              <div><p className="eyebrow">For auditors</p><h2>Technical details</h2></div>
            </summary>
            <div className="case-facts case-facts-flat">
              <MetaItem label="Jurisdiction" value={employee.jurisdiction} />
              <MetaItem label="Case ID" value={String(exception.id)} mono />
              {detail.data.source_version ? <MetaItem label="Analyzed source version" value={detail.data.source_version} mono /> : null}
              {proposal ? <MetaItem label="Suggestion source" value={`${proposal.model} · ${proposal.latency_ms} ms · attempt ${proposal.attempts}`} mono /> : null}
            </div>
          </details>
        </div>
      </div>
    </main>
  );
}

function MetaItem({ label, value, mono = false }: { label: string; value: string | null | undefined; mono?: boolean }) {
  return <div className="meta-item"><span>{label}</span><strong className={mono ? "mono" : ""}>{value || "Not provided"}</strong></div>;
}
