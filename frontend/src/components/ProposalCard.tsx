import { useState } from "react";
import type { ExceptionOut, ProposalOut } from "../api/schemas";
import { ErrorBanner, formatDateShort, formatTime } from "./common";
import { IconBot } from "./icons";

const VALIDATOR_IDS = Array.from({ length: 10 }, (_, index) => `V-${String(index + 1).padStart(2, "0")}`);

type Check = ProposalOut["validation"]["checks"][number];

function CheckItem({ check }: { check: Check }) {
  const warning = check.level === "warning";
  return (
    <li className={`${check.passed ? "validation-passed" : "validation-failed"} ${warning ? "validation-warning" : ""}`}>
      <span aria-hidden="true">{warning ? "⚠" : check.passed ? "✓" : "✕"}</span>
      <code>{check.id}</code>
      <span>{check.name}<small>{check.detail}</small></span>
    </li>
  );
}

export function ValidationChecklist({ validation }: { validation: ProposalOut["validation"] }) {
  const byId = new Map(validation.checks.map((check) => [check.id, check]));
  const standardPresent = VALIDATOR_IDS.filter((id) => byId.has(id)).length;
  const passedStandard = VALIDATOR_IDS.filter((id) => byId.get(id)?.passed).length;
  const unavailable = VALIDATOR_IDS.length - standardPresent;
  const needAttention = validation.checks.filter((check) => !check.passed).length;
  const additional = validation.checks.filter((check) => !VALIDATOR_IDS.includes(check.id));

  let summary: string;
  if (standardPresent === 0) summary = "No AI suggestion to check";
  else if (needAttention > 0) summary = `${needAttention} check${needAttention === 1 ? "" : "s"} need${needAttention === 1 ? "s" : ""} attention`;
  else if (unavailable > 0) summary = `${passedStandard} of ${VALIDATOR_IDS.length} passed · ${unavailable} unavailable`;
  else summary = `All ${VALIDATOR_IDS.length} checks passed`;

  return (
    <section className="validation-section" aria-labelledby="validation-heading">
      <details className={`validation-details ${standardPresent === 0 ? "is-na" : validation.passed ? "is-pass" : "is-fail"}`} open={!validation.passed && standardPresent > 0}>
        <summary>
          <span className="validation-badge" aria-hidden="true">{standardPresent === 0 ? "–" : validation.passed ? "✓" : "!"}</span>
          <span className="validation-summary-text">
            <strong id="validation-heading">Safety checks</strong>
            <small>{summary}</small>
          </span>
          <span className="validation-toggle" aria-hidden="true" />
        </summary>
        <ul className="validation-list">
          {VALIDATOR_IDS.map((id) => {
            const check = byId.get(id);
            if (!check) {
              return <li className="validation-unavailable" key={id}><span aria-hidden="true">⚠</span><code>{id}</code><span>Result unavailable</span></li>;
            }
            return <CheckItem key={id} check={check} />;
          })}
        </ul>
        {additional.length ? (
          <ul className="validation-list validation-additional">
            {additional.map((check) => <CheckItem key={check.id} check={check} />)}
          </ul>
        ) : null}
        {validation.side_effects.length ? (
          <div className="side-effects">
            <strong>Potential side effects</strong>
            <ul>{validation.side_effects.map((effect, index) => <li key={`${effect.rule_id}-${effect.work_date}-${index}`}><b>{effect.rule_id} · {effect.severity}</b> — {effect.summary}</li>)}</ul>
          </div>
        ) : null}
      </details>
    </section>
  );
}

export function ProposalCard({
  exception,
  proposal,
  onApprove,
  onPolicyClick,
  showActions = true,
  policyTitles,
}: {
  exception: ExceptionOut;
  proposal: ProposalOut;
  onApprove?: () => Promise<unknown> | void;
  onPolicyClick?: (policyId: string) => void;
  showActions?: boolean;
  policyTitles?: Record<string, string>;
}) {
  const [error, setError] = useState<unknown>(null);
  const correction = proposal.proposal.proposed_correction;
  const escalated = correction.action === "ESCALATE";
  const fallback = proposal.source === "fallback";
  const showConfidence = !escalated && !fallback;
  const editable = exception.constraints.allowed_actions.some((action) => action !== "ESCALATE");

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
      <div className={`ai-banner${fallback ? " is-fallback" : ""}`} title={`${proposal.model} · ${proposal.latency_ms} ms`}>
        <span className="ai-banner-icon" aria-hidden="true">{fallback ? "!" : <IconBot />}</span>
        <span className="ai-banner-text">
          <strong>{fallback ? "Fallback: escalated, no AI suggestion" : "AI-assisted suggestion"}</strong>
          <small>{fallback
            ? "The AI could not produce a safe correction, so a person needs to decide."
            : "Drafted by an AI model. A person makes the final decision."}</small>
        </span>
      </div>
      <div className="proposal-topline">
        <p className="eyebrow">Recommended action</p>
      </div>
      <h2 id="proposal-heading" className="proposal-headline">{headlineFor(correction)}</h2>
      <p className="proposal-explanation">{proposal.proposal.explanation}</p>

      {escalated ? (
        <div className="proposal-notice">
          {editable
            ? "This case can’t be corrected automatically. Review the evidence, then edit the correction yourself or reject this suggestion with a note."
            : "Company policy doesn’t allow an automatic correction for this case. Review the evidence and fix it in your timekeeping system, then reject this suggestion with a note to close the case."}
        </div>
      ) : (
        <div className="correction-diff" aria-label="Proposed timecard change">
          <div><span>Current record</span><strong>{describeBefore(exception, correction.action)}</strong></div>
          <span className="diff-arrow" aria-hidden="true">→</span>
          <div><span>Suggested change</span><strong>{describeAfter(exception, correction)}</strong></div>
        </div>
      )}

      {showConfidence || proposal.proposal.policy_ids_cited.length ? (
        <div className="proposal-facts">
          {showConfidence ? <div className="proposal-fact"><span>Confidence</span><strong>{Math.round(proposal.proposal.confidence * 100)}%</strong></div> : null}
          {proposal.proposal.policy_ids_cited.length ? (
            <div className="proposal-fact proposal-policies">
              <span>Policy applied</span>
              <div>
                {proposal.proposal.policy_ids_cited.map((policyId) => (
                  <button type="button" className="policy-chip" key={policyId} title={`Open policy ${policyId}`} onClick={() => onPolicyClick?.(policyId)}>
                    {policyTitles?.[policyId] ?? policyId} ↗
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      <ValidationChecklist validation={proposal.validation} />
      {error ? <ErrorBanner error={error} /> : null}
      {showActions ? (
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
      ) : null}
    </article>
  );
}

const KIND_LABEL: Record<string, string> = {
  IN: "clock-in",
  OUT: "clock-out",
  BREAK_START: "break start",
  BREAK_END: "break end",
};

function headlineFor(correction: ProposalOut["proposal"]["proposed_correction"]): string {
  const when = correction.ts ? formatTime(correction.ts) : "a time to confirm";
  switch (correction.action) {
    case "ADD_PUNCH": return `Add a ${KIND_LABEL[correction.kind ?? ""] ?? "punch"} at ${when}`;
    case "MODIFY_PUNCH": return `Change punch #${correction.punch_id} to ${when}`;
    case "REMOVE_PUNCH": return `Remove duplicate punch #${correction.punch_id}`;
    case "ADJUST_SCHEDULE": return `Adjust the scheduled end to ${when}`;
    case "FLAG_FOR_APPROVAL": return "Flag this case for approval";
    default: return "No automatic fix: your review is needed";
  }
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
  if (correction.action === "FLAG_FOR_APPROVAL") return "No timecard change: flag for approval";
  return "No timecard change: route for review";
}
