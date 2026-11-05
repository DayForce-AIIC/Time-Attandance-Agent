import { useState } from "react";
import { useAudit, useVerifyAudit } from "../api/hooks";
import { ErrorBanner, LoadingState } from "./common";

type Tone = "info" | "ok" | "warn" | "neutral";

const EVENT_INFO: Record<string, { label: string; help?: string; tone: Tone }> = {
  EXCEPTION_DETECTED: { label: "Exception detected", help: "The system flagged this timecard for review.", tone: "info" },
  DETERMINISTIC_DETECTION_COMPLETED: { label: "Attendance rules checked", tone: "neutral" },
  PROMPT_BUILT: { label: "Suggestion requested", help: "The case facts and relevant policy were prepared for the AI model.", tone: "neutral" },
  LLM_CALL: { label: "AI model responded", tone: "neutral" },
  LLM_ERROR: { label: "AI model error", help: "The model could not produce a usable answer on this attempt.", tone: "warn" },
  LLM_UNAVAILABLE: { label: "AI model unavailable", tone: "warn" },
  SCHEMA_VALIDATION: { label: "Response format checked", tone: "neutral" },
  VALIDATION_RESULT: { label: "Safety checks completed", tone: "neutral" },
  RETRY: { label: "Suggestion retried", help: "The first answer did not pass the safety checks, so it was tried again.", tone: "warn" },
  FALLBACK: { label: "Escalated: no automatic fix", help: "No safe suggestion was produced, so this case needs your judgment.", tone: "warn" },
  PROPOSAL_CREATED: { label: "Recommendation created", tone: "info" },
  DECISION_MADE: { label: "Decision recorded", tone: "ok" },
  CORRECTION_APPLIED: { label: "Correction applied", help: "The approved change was written to the timecard.", tone: "ok" },
  SOURCE_CHANGED_BEFORE_REVIEW: { label: "Attendance changed before review", tone: "warn" },
  SOURCE_CHANGED_BEFORE_WRITEBACK: { label: "Attendance changed before saving", tone: "warn" },
  SOURCE_RECORD_DELETED: { label: "Source record deleted", tone: "warn" },
  EXCEPTION_SUPERSEDED: { label: "Replaced by a newer exception", tone: "neutral" },
  ANALYSIS_RETRY_SCHEDULED: { label: "Analysis will be retried", tone: "warn" },
  ANALYSIS_FAILED: { label: "Analysis failed", tone: "warn" },
  ANALYSIS_CANCELLED_STALE_SOURCE: { label: "Analysis cancelled: attendance changed", tone: "warn" },
  FAULT_MODE_CHANGED: { label: "Demo fault setting changed", tone: "neutral" },
  VOID_PUNCH: { label: "Punch voided", tone: "neutral" },
};

/** Plain-language label for an audit event type; unknown types are made readable. */
export function describeAuditEvent(type: string): { label: string; help?: string; tone: Tone } {
  const known = EVENT_INFO[type];
  if (known) return known;
  const text = type.replaceAll("_", " ").toLowerCase();
  return { label: text.charAt(0).toUpperCase() + text.slice(1), tone: "neutral" };
}

function formatWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
}

export function AuditDrawer({ exceptionId }: { exceptionId: number | string }) {
  const [open, setOpen] = useState(false);
  const [verifyRequested, setVerifyRequested] = useState(false);
  const events = useAudit(exceptionId, open);
  const verification = useVerifyAudit(open && verifyRequested);

  const verify = () => {
    setVerifyRequested(true);
    void verification.refetch();
  };

  return (
    <>
      <button type="button" className="button button-quiet audit-trigger" onClick={() => setOpen(true)}>◷ Audit trail</button>
      {open ? (
        <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
          <aside className="audit-drawer" role="dialog" aria-modal="true" aria-labelledby="audit-heading">
            <div className="drawer-heading">
              <div><p className="eyebrow">Case history</p><h2 id="audit-heading">Audit trail</h2></div>
              <button type="button" className="icon-button" aria-label="Close audit trail" onClick={() => setOpen(false)}>×</button>
            </div>
            <p className="drawer-intro">Every step on this case is recorded here, in order. The history is tamper-evident: if a past entry were changed, the integrity check below would flag it.</p>
            {events.isError ? <ErrorBanner error={events.error} onRetry={() => void events.refetch()} /> : null}
            {events.isLoading ? <LoadingState label="Loading audit events…" /> : (
              <ol className="audit-list">
                {(events.data ?? []).map((event) => {
                  const info = describeAuditEvent(event.event_type);
                  return (
                    <li key={event.id} className="audit-event">
                      <span className={`audit-dot tone-${info.tone}`} aria-hidden="true" />
                      <div className="audit-event-body">
                        <strong>{info.label}</strong>
                        <time dateTime={event.created_at}>{formatWhen(event.created_at)}</time>
                        {info.help ? <p className="audit-event-help">{info.help}</p> : null}
                        <details className="audit-tech">
                          <summary>Technical details</summary>
                          <p className="audit-hash">Hash · {event.hash.slice(0, 16)}…</p>
                          <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                        </details>
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
            <div className="verify-box">
              <button type="button" className="button button-primary" onClick={verify} disabled={verification.isFetching}>
                {verification.isFetching ? "Verifying…" : "Verify history integrity"}
              </button>
              {verification.isError ? <ErrorBanner error={verification.error} onRetry={verify} /> : null}
              {verification.data ? (
                <p className={verification.data.ok ? "verify-ok" : "verify-fail"} role="status">
                  <span aria-hidden="true">{verification.data.ok ? "✓" : "✕"}</span>{" "}
                  {verification.data.ok
                    ? `History verified: ${verification.data.events_checked} entries, none altered`
                    : `Integrity check failed at entry ${verification.data.first_bad_event_id ?? "unknown"}`}
                </p>
              ) : null}
            </div>
          </aside>
        </div>
      ) : null}
    </>
  );
}
