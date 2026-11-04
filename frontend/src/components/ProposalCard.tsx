import { useState } from "react";
import type { ExceptionOut, ProposalOut } from "../api/schemas";
import { ErrorBanner, formatDateShort, formatTime } from "./common";
import { useAuth } from "../auth/AuthContext";

const VALIDATOR_IDS = Array.from({ length: 10 }, (_, index) => `V-${String(index + 1).padStart(2, "0")}`);

export function ValidationChecklist({ validation }: { validation: ProposalOut["validation"] }) {
  const byId = new Map(validation.checks.map((check) => [check.id, check]));
  const standard = VALIDATOR_IDS.map((id) => {
    const check = byId.get(id);
    if (!check) {
      return <li className="validation-unavailable" key={id}><span aria-hidden="true">⚠</span><code>{id}</code><span>Result unavailable</span></li>;
    }
    const warning = check.level === "warning";
    return (
      <li className={`${check.passed ? "validation-passed" : "validation-failed"} ${warning ? "validation-warning" : ""}`} key={id}>
        <span aria-hidden="true">{warning ? "⚠" : check.passed ? "✓" : "✕"}</span>
        <code>{check.id}</code>
        <span>{check.name}<small>{check.detail}</small></span>
      </li>
    );
  });
  const additional = validation.checks.filter((check) => !VALIDATOR_IDS.includes(check.id));

  return (
    <section className="validation-section" aria-labelledby="validation-heading">
      <div className="subsection-heading"><h3 id="validation-heading">Safety checks</h3><span>{validation.passed ? "Passed" : "Review needed"}</span></div>
      <ul className="validation-list">{standard}</ul>
      {additional.length ? (
        <ul className="validation-list validation-additional">
          {additional.map((check) => (
            <li className={`${check.passed ? "validation-passed" : "validation-failed"} ${check.level === "warning" ? "validation-warning" : ""}`} key={check.id}>
              <span aria-hidden="true">{check.level === "warning" ? "⚠" : check.passed ? "✓" : "✕"}</span>
              <code>{check.id}</code><span>{check.name}<small>{check.detail}</small></span>
            </li>
          ))}
        </ul>
      ) : null}
      {validation.side_effects.length ? (
        <div className="side-effects">
          <strong>Potential side effects</strong>
          <ul>{validation.side_effects.map((effect, index) => <li key={`${effect.rule_id}-${effect.work_date}-${index}`}><b>{effect.rule_id} · {effect.severity}</b> — {effect.summary}</li>)}</ul>
        </div>
      ) : null}
    </section>
  );
}

export function ProposalCard({
  exception,
  proposal,
  onApprove,
  onPolicyClick,
}: {
  exception: ExceptionOut;
  proposal: ProposalOut;
  onApprove?: () => Promise<unknown> | void;
  onPolicyClick?: (policyId: string) => void;
}) {
  const auth = useAuth();
  const [error, setError] = useState<unknown>(null);
  const correction = proposal.proposal.proposed_correction;
  const escalated = correction.action === "ESCALATE";

  const approve = async () => {
    if (!onApprove || escalated) return;
    setError(null);
    try {
      await onApprove();
    } catch (cause) {
      setError(cause);
    }
  };

  return (
    <article className="panel proposal-card" aria-labelledby="proposal-heading">
      <div className="proposal-topline">
        <span className={`source-badge ${proposal.source === "fallback" ? "source-fallback" : "source-llm"}`}>
          <span aria-hidden="true">{proposal.source === "fallback" ? "!" : "✦"}</span>{" "}
          {proposal.source === "fallback" ? "Fallback: escalated, no AI suggestion" : "AI proposal"}
        </span>
        <span className="proposal-meta">{proposal.model} · {proposal.latency_ms} ms</span>
      </div>
      <div className="proposal-heading-row">
        <div><p className="eyebrow">Validated suggestion</p><h2 id="proposal-heading">{auth.enabled ? "HR review" : "Manager review"}</h2></div>
        <span className="attempt-pill">{proposal.attempts === 1 ? "Passed on first attempt" : `Passed on attempt ${proposal.attempts}`}</span>
      </div>
      <p className="proposal-explanation">{proposal.proposal.explanation}</p>

      <div className="correction-diff" aria-label="Proposed timecard change">
        <div><span>Before</span><strong>{describeBefore(exception, correction.action)}</strong></div>
        <span className="diff-arrow" aria-hidden="true">→</span>
        <div><span>After</span><strong>{describeAfter(exception, correction)}</strong></div>
      </div>

      <div className="confidence-row">
        <span>Model confidence</span>
        <progress max={1} value={proposal.proposal.confidence} aria-label={`Model confidence ${Math.round(proposal.proposal.confidence * 100)} percent`} />
        <strong>{Math.round(proposal.proposal.confidence * 100)}%</strong>
      </div>

      <div className="cited-policies">
        <span>Cited policies</span>
        <div>{proposal.proposal.policy_ids_cited.map((policyId) => (
          <button type="button" className="policy-chip" key={policyId} onClick={() => onPolicyClick?.(policyId)}>{policyId} ↗</button>
        ))}</div>
      </div>

      <ValidationChecklist validation={proposal.validation} />
      {error ? <ErrorBanner error={error} /> : null}
      <div className="proposal-approve-row">
        <button
          type="button"
          className="button button-primary"
          onClick={() => void approve()}
          disabled={escalated || !onApprove}
          title={escalated ? "An escalation has no timecard change to approve." : undefined}
        >
          Approve suggestion
        </button>
        {escalated ? <span className="muted-note">Escalation requires a manager action; there is no correction to approve.</span> : null}
      </div>
    </article>
  );
}

function describeBefore(exception: ExceptionOut, action: string): string {
  const evidence = exception.evidence;
  if (action === "ADD_PUNCH") return String(evidence.observed ?? "Punch missing from the timecard");
  if (action === "MODIFY_PUNCH") return String(evidence.observed_ts ?? evidence.recorded_out ?? "Current punch time");
  if (action === "REMOVE_PUNCH") return "Duplicate punch recorded";
  if (action === "ADJUST_SCHEDULE") return String(evidence.scheduled_end ?? "Current scheduled end");
  if (action === "FLAG_FOR_APPROVAL") return "No timecard change recorded";
  return "No timecard change suggested";
}

function describeAfter(exception: ExceptionOut, correction: ProposalOut["proposal"]["proposed_correction"]): string {
  if (correction.action === "ADD_PUNCH") {
    const time = correction.ts ? formatTime(correction.ts) : "a time to confirm";
    return `Add ${correction.kind?.replace("_", " ")} at ${time} · ${formatDateShort(exception.work_date)}`;
  }
  if (correction.action === "MODIFY_PUNCH") return `Change punch #${correction.punch_id} to ${correction.ts ? formatTime(correction.ts) : "a new time"}`;
  if (correction.action === "REMOVE_PUNCH") return `Remove duplicate punch #${correction.punch_id}`;
  if (correction.action === "ADJUST_SCHEDULE") return `Adjust schedule end to ${correction.ts ? formatTime(correction.ts) : "a new time"}`;
  if (correction.action === "FLAG_FOR_APPROVAL") return "No timecard change: flag for manager approval";
  return "No timecard change: route for manager review";
}
