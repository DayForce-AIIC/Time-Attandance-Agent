import { Link } from "react-router-dom";
import type { DecisionOut, ProposedCorrection } from "../api/schemas";
import { ErrorBanner, formatTime, StatusBadge } from "./common";
import { MANAGER_NAMES } from "./ManagerSwitcher";

type Tone = "ok" | "warn" | "err" | "neutral";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const KIND_LABEL: Record<string, string> = {
  IN: "clock-in",
  OUT: "clock-out",
  BREAK_START: "break start",
  BREAK_END: "break end",
};

const WRITEBACK: Record<DecisionOut["writeback_status"], { tone: Tone; label: string }> = {
  applied: { tone: "ok", label: "Saved to the timekeeping system" },
  pending: { tone: "warn", label: "Saving to the timekeeping system…" },
  failed: { tone: "err", label: "Couldn’t save to the timekeeping system" },
  stale: { tone: "err", label: "Not saved: attendance changed after review" },
  not_requested: { tone: "neutral", label: "No timekeeping update needed" },
};

const ICON: Record<Tone, string> = { ok: "✓", warn: "…", err: "!", neutral: "✕" };

/** Turns a raw reviewer id into something a person can read. */
export function reviewerLabel(managerId: string, currentUserId?: string | null): string {
  if (currentUserId && managerId === currentUserId) return "you";
  const named = (MANAGER_NAMES as Record<string, string>)[managerId];
  if (named) return named;
  return UUID.test(managerId) ? "another HR reviewer" : managerId;
}

/** Describes the final correction in a sentence instead of showing raw JSON. */
export function describeFinalCorrection(correction: ProposedCorrection | null | undefined): string {
  if (!correction) return "No timecard change was made.";
  const when = correction.ts ? formatTime(correction.ts) : "the confirmed time";
  switch (correction.action) {
    case "ADD_PUNCH": return `Added a ${KIND_LABEL[correction.kind ?? ""] ?? "punch"} at ${when}.`;
    case "MODIFY_PUNCH": return `Changed punch #${correction.punch_id} to ${when}.`;
    case "REMOVE_PUNCH": return `Removed duplicate punch #${correction.punch_id}.`;
    case "ADJUST_SCHEDULE": return `Adjusted the scheduled end to ${when}.`;
    case "FLAG_FOR_APPROVAL": return "Flagged for approval. No timecard change was needed.";
    default: return "Routed for review. No timecard change was made.";
  }
}

function formatDecisionTime(value: string, utc: boolean): { date: string; time: string } {
  const when = new Date(utc ? `${value}:00Z` : value);
  if (Number.isNaN(when.getTime())) return { date: value.slice(0, 10), time: value.slice(11, 16) };
  return {
    date: new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(when),
    time: new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(when),
  };
}

export function DecisionSummary({
  decision,
  currentUserId,
  utc = false,
  onRetryWriteback,
  retrying = false,
  retryError,
}: {
  decision: DecisionOut;
  currentUserId?: string | null;
  /** HR-mode timestamps are stored in UTC without an offset; demo timestamps are local. */
  utc?: boolean;
  onRetryWriteback?: () => void;
  retrying?: boolean;
  retryError?: unknown;
}) {
  const rejected = decision.action === "reject";
  const changesTimecard = !rejected
    && decision.final_correction !== null
    && ["ADD_PUNCH", "MODIFY_PUNCH", "REMOVE_PUNCH", "ADJUST_SCHEDULE"].includes(decision.final_correction.action);
  // With no external timekeeping system (demo), the change is applied directly to the timecard.
  const writeback = decision.writeback_status === "not_requested" && changesTimecard
    ? { tone: "ok" as Tone, label: "Applied to the timecard" }
    : WRITEBACK[decision.writeback_status];
  const tone: Tone = rejected ? "neutral" : writeback.tone === "neutral" ? "ok" : writeback.tone;
  const heading = rejected
    ? "Suggestion rejected"
    : decision.writeback_status === "failed" ? "Approved, but saving failed"
      : decision.writeback_status === "stale" ? "Approved, but attendance changed"
        : decision.writeback_status === "pending" ? "Approved, saving in progress"
          : "Correction approved";
  const outcome = rejected ? "Rejected" : decision.action === "edit" ? "Approved with edits" : "Approved as suggested";
  const when = formatDecisionTime(decision.decided_at, utc);
  const canRetry = Boolean(onRetryWriteback) && (decision.writeback_status === "pending" || decision.writeback_status === "failed");

  return (
    <section className="panel decision-summary" aria-labelledby="decision-summary-heading">
      <div className={`decision-banner is-${tone}`}>
        <span className="decision-banner-icon" aria-hidden="true">{ICON[tone]}</span>
        <div>
          <p className="eyebrow">Decision recorded</p>
          <h2 id="decision-summary-heading">{heading}</h2>
          <p className="decision-banner-meta">Decided by {reviewerLabel(decision.manager_id, currentUserId)} on {when.date} at {when.time}</p>
        </div>
      </div>

      <div className="decision-facts">
        <div><span>Outcome</span><strong>{outcome}</strong></div>
        <div><span>What changed</span><strong>{describeFinalCorrection(rejected ? null : decision.final_correction)}</strong></div>
        <div><span>Case status</span><strong><StatusBadge status={decision.resulting_status} /></strong></div>
        <div><span>Timekeeping system</span><strong><span className={`writeback-tag is-${writeback.tone}`}>{writeback.label}</span></strong></div>
      </div>

      {decision.note ? <blockquote className="decision-note"><span>Reviewer note</span>{decision.note}</blockquote> : null}

      {canRetry ? (
        <div className="decision-retry">
          <p className="muted-note">Your decision is saved. You can safely try saving the change to the timekeeping system again.</p>
          {retryError ? <ErrorBanner error={retryError} /> : null}
          <button type="button" className="button button-primary" onClick={onRetryWriteback} disabled={retrying}>
            {retrying ? "Retrying…" : "Retry saving"}
          </button>
        </div>
      ) : null}

      <div className="decision-footer">
        <Link className="button button-primary" to="/">Back to queue</Link>
      </div>

      <details className="decision-tech">
        <summary>Technical details</summary>
        <div className="decision-tech-grid">
          <div><span>Decision ID</span><code>{String(decision.id)}</code></div>
          <div><span>Reviewer ID</span><code>{decision.manager_id}</code></div>
          {decision.external_operation_id ? <div><span>Timekeeping operation</span><code>{decision.external_operation_id}</code></div> : null}
        </div>
        {decision.final_correction ? <pre className="decision-correction">{JSON.stringify(decision.final_correction, null, 2)}</pre> : null}
      </details>
    </section>
  );
}
