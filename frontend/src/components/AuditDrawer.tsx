import { useState } from "react";
import { useAudit, useVerifyAudit } from "../api/hooks";
import { ErrorBanner, LoadingState } from "./common";

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
      <button type="button" className="button button-outline" onClick={() => setOpen(true)}>◷ Audit trail</button>
      {open ? (
        <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
          <aside className="audit-drawer" role="dialog" aria-modal="true" aria-labelledby="audit-heading">
            <div className="drawer-heading">
              <div><p className="eyebrow">Append-only history</p><h2 id="audit-heading">Audit trail</h2></div>
              <button type="button" className="icon-button" aria-label="Close audit trail" onClick={() => setOpen(false)}>×</button>
            </div>
            <p className="drawer-intro">Events are linked by a SHA-256 hash chain and recorded for this case.</p>
            {events.isError ? <ErrorBanner error={events.error} onRetry={() => void events.refetch()} /> : null}
            {events.isLoading ? <LoadingState label="Loading audit events…" /> : (
              <ol className="audit-list">
                {(events.data ?? []).map((event) => (
                  <li key={event.id} className="audit-event">
                    <div className="audit-event-top">
                      <span className="audit-dot" aria-hidden="true" />
                      <div><strong>{event.event_type.replaceAll("_", " ").toLowerCase()}</strong><time>{event.created_at.replace("T", " ").replace("Z", " UTC")}</time></div>
                    </div>
                    <p className="audit-hash">Hash · {event.hash.slice(0, 16)}…</p>
                    <details>
                      <summary>Event payload</summary>
                      <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                    </details>
                  </li>
                ))}
              </ol>
            )}
            <div className="verify-box">
              <button type="button" className="button button-primary" onClick={verify} disabled={verification.isFetching}>
                {verification.isFetching ? "Verifying…" : "Verify chain"}
              </button>
              {verification.isError ? <ErrorBanner error={verification.error} onRetry={verify} /> : null}
              {verification.data ? (
                <p className={verification.data.ok ? "verify-ok" : "verify-fail"} role="status">
                  <span aria-hidden="true">{verification.data.ok ? "✓" : "✕"}</span>{" "}
                  {verification.data.ok
                    ? `Chain intact (${verification.data.events_checked} events)`
                    : `Chain failed at event ${verification.data.first_bad_event_id ?? "unknown"}`}
                </p>
              ) : null}
            </div>
          </aside>
        </div>
      ) : null}
    </>
  );
}
