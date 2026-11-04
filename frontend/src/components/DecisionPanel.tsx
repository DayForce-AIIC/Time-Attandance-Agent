import { useState } from "react";
import type { DecisionBody } from "../api/hooks";
import type { DecisionOut, ExceptionOut, ProposalOut, ProposedCorrection } from "../api/schemas";
import { ErrorBanner } from "./common";
import { EditCorrectionForm } from "./EditCorrectionForm";

export function DecisionPanel({
  exception,
  proposal,
  onDecision,
}: {
  exception: ExceptionOut;
  proposal: ProposalOut;
  onDecision: (body: DecisionBody) => Promise<DecisionOut>;
}) {
  const [mode, setMode] = useState<"idle" | "edit" | "reject">("idle");
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submitCorrection = async (correction: ProposedCorrection) => {
    setError(null);
    const result = await onDecision({ action: "edit", edited_correction: correction });
    setMessage(`Edit recorded. Case status: ${result.resulting_status.toLowerCase().replaceAll("_", " ")}.`);
    setMode("idle");
    return result;
  };

  const submitReject = async () => {
    if (!note.trim()) {
      setError(new Error("A note is required when rejecting this proposal."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await onDecision({ action: "reject", note: note.trim() });
      setMessage(`Proposal rejected. Case status: ${result.resulting_status.toLowerCase()}.`);
      setMode("idle");
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel decision-panel" aria-labelledby="decision-heading">
      <div className="panel-heading">
        <div><p className="eyebrow">Human decision</p><h2 id="decision-heading">Review and decide</h2></div>
        <span className="decision-required">Manager approval required</span>
      </div>
      {error ? <ErrorBanner error={error} /> : null}
      {message ? <p className="success-note" role="status">✓ {message}</p> : null}
      {mode === "edit" ? (
        <EditCorrectionForm
          exception={exception}
          initialCorrection={proposal.proposal.proposed_correction}
          onSubmit={submitCorrection}
          onCancel={() => setMode("idle")}
        />
      ) : mode === "reject" ? (
        <div className="reject-form">
          <label><span>Rejection note <em>Required</em></span><textarea aria-label="Rejection note" rows={3} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Explain why this suggestion was rejected" /></label>
          <div className="form-actions">
            <button type="button" className="button button-quiet" onClick={() => { setMode("idle"); setError(null); }}>Cancel</button>
            <button type="button" className="button button-danger" onClick={() => void submitReject()} disabled={busy}>{busy ? "Rejecting…" : "Confirm rejection"}</button>
          </div>
        </div>
      ) : (
        <div className="decision-actions">
          <button type="button" className="button button-outline" onClick={() => { setError(null); setMode("edit"); }}>Edit correction</button>
          <button type="button" className="button button-danger-outline" onClick={() => { setError(null); setMode("reject"); }}>Reject</button>
        </div>
      )}
    </section>
  );
}
