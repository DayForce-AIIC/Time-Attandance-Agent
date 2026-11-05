import { useState } from "react";
import type { DecisionBody } from "../api/hooks";
import type { DecisionOut, ExceptionOut, ProposalOut, ProposedCorrection } from "../api/schemas";
import { ErrorBanner } from "./common";
import { EditCorrectionForm } from "./EditCorrectionForm";
import { SlideOver } from "./SlideOver";
import { useAuth } from "../auth/AuthContext";

export function DecisionPanel({
  exception,
  proposal,
  onDecision,
  onApprove,
}: {
  exception: ExceptionOut;
  proposal: ProposalOut;
  onDecision: (body: DecisionBody) => Promise<DecisionOut>;
  onApprove?: () => Promise<unknown>;
}) {
  const auth = useAuth();
  const [mode, setMode] = useState<"idle" | "edit" | "reject">("idle");
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const escalated = proposal.proposal.proposed_correction.action === "ESCALATE";
  // Some rules (for example an unparseable punch sequence) allow escalation only, so there is nothing to edit.
  const editable = exception.constraints.allowed_actions.some((action) => action !== "ESCALATE");

  const closeForm = () => {
    setMode("idle");
    setError(null);
  };

  const submitApprove = async () => {
    if (!onApprove) return;
    setBusy(true);
    setError(null);
    try {
      await onApprove();
      setMessage("Correction approved.");
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  };

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
        <div><p className="eyebrow">Your decision</p><h2 id="decision-heading">Review and decide</h2></div>
        <span className="decision-required">{auth.enabled ? "HR approval required" : "Manager approval required"}</span>
      </div>
      {error && mode === "idle" ? <ErrorBanner error={error} /> : null}
      {message ? <p className="success-note" role="status">✓ {message}</p> : null}

      <div className="decision-actions">
        {onApprove && !escalated ? (
          <button
            type="button"
            className="button button-primary decision-approve"
            onClick={() => void submitApprove()}
            disabled={busy}
          >
            {busy ? "Approving…" : "Approve suggestion"}
          </button>
        ) : null}
        {editable ? (
          <button type="button" className={`button ${escalated ? "button-primary" : "button-outline"}`} onClick={() => { setError(null); setMode("edit"); }}>Edit correction</button>
        ) : null}
        <button type="button" className="button button-danger-outline" onClick={() => { setError(null); setMode("reject"); }}>{editable ? "Reject" : "Reject suggestion"}</button>
      </div>
      {escalated ? (
        <p className="decision-hint">
          {editable
            ? "There is no automatic correction to approve for this case. Enter the correct time yourself, or reject the suggestion with a note."
            : "There is no automatic correction to approve, and this rule can only be escalated. Reject the suggestion with a note to record your decision."}
        </p>
      ) : null}

      {mode === "edit" ? (
        <SlideOver eyebrow="Your decision" title="Edit correction" closeLabel="Close editor" onClose={closeForm}>
          <EditCorrectionForm
            exception={exception}
            initialCorrection={proposal.proposal.proposed_correction}
            onSubmit={submitCorrection}
            onCancel={closeForm}
          />
        </SlideOver>
      ) : null}
      {mode === "reject" ? (
        <SlideOver eyebrow="Your decision" title="Reject suggestion" closeLabel="Close" onClose={closeForm}>
          {error ? <ErrorBanner error={error} /> : null}
          <div className="reject-form">
            <label><span>Rejection note <em>Required</em></span><textarea aria-label="Rejection note" rows={4} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Explain why this suggestion was rejected" /></label>
            <div className="form-actions">
              <button type="button" className="button button-quiet" onClick={closeForm}>Cancel</button>
              <button type="button" className="button button-danger" onClick={() => void submitReject()} disabled={busy}>{busy ? "Rejecting…" : "Confirm rejection"}</button>
            </div>
          </div>
        </SlideOver>
      ) : null}
    </section>
  );
}
