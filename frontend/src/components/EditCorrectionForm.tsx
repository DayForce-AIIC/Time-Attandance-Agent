import { useState, type FormEvent } from "react";
import { actionSchema, proposedCorrectionSchema, punchKindSchema, type Action, type ExceptionOut, type ProposedCorrection, type ProposalOut } from "../api/schemas";
import { ErrorBanner } from "./common";

export function EditCorrectionForm({
  exception,
  initialCorrection,
  onSubmit,
  onCancel,
}: {
  exception: ExceptionOut;
  initialCorrection: ProposedCorrection | null;
  onSubmit: (correction: ProposedCorrection) => Promise<unknown>;
  onCancel?: () => void;
}) {
  const editableActions = exception.constraints.allowed_actions.filter((action) => action !== "ESCALATE") as Exclude<Action, "ESCALATE">[];
  const defaultAction = initialCorrection?.action && editableActions.includes(initialCorrection.action as Exclude<Action, "ESCALATE">)
    ? initialCorrection.action
    : editableActions[0] ?? "ESCALATE";
  const [action, setAction] = useState<Action>(defaultAction);
  const [kind, setKind] = useState<ProposalOut["proposal"]["proposed_correction"]["kind"]>(initialCorrection?.kind ?? exception.constraints.target_kind ?? "OUT");
  const [ts, setTs] = useState(initialCorrection?.ts ?? exception.constraints.ts_window?.earliest ?? "");
  const [punchId, setPunchId] = useState(String(initialCorrection?.punch_id ?? exception.constraints.target_punch_ids[0] ?? ""));
  const [scheduleId, setScheduleId] = useState(String(initialCorrection?.schedule_id ?? exception.constraints.target_schedule_id ?? ""));
  const [formError, setFormError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError(null);
    setApiError(null);
    const base = { action };
    const raw: Record<string, unknown> = { ...base };
    if (action === "ADD_PUNCH") Object.assign(raw, { kind, ts });
    if (action === "MODIFY_PUNCH") Object.assign(raw, { punch_id: Number(punchId), ts });
    if (action === "REMOVE_PUNCH") Object.assign(raw, { punch_id: Number(punchId) });
    if (action === "ADJUST_SCHEDULE") Object.assign(raw, { schedule_id: Number(scheduleId), ts });
    const parsed = proposedCorrectionSchema.safeParse(raw);
    if (!parsed.success) {
      setFormError(parsed.error.issues[0]?.message ?? "Check the correction details.");
      return;
    }
    setSaving(true);
    try {
      await onSubmit(parsed.data);
    } catch (error) {
      setApiError(error);
    } finally {
      setSaving(false);
    }
  };

  const actionHasTime = ["ADD_PUNCH", "MODIFY_PUNCH", "ADJUST_SCHEDULE"].includes(action);
  return (
    <form className="edit-correction-form" onSubmit={(event) => void submit(event)}>
      <div className="subsection-heading"><div><p className="eyebrow">Manager edit</p><h3>Edit correction</h3></div></div>
      {editableActions.length ? (
        <label>
          <span>Correction action</span>
          <select aria-label="Correction action" value={action} onChange={(event) => setAction(actionSchema.parse(event.target.value))}>
            {editableActions.map((allowed) => <option key={allowed} value={allowed}>{allowed.replaceAll("_", " ").toLowerCase()}</option>)}
          </select>
        </label>
      ) : <p className="muted-note">This case only allows escalation and has no editable correction.</p>}

      {action === "ADD_PUNCH" ? (
        <label><span>Punch type</span><select aria-label="Punch type" value={kind ?? "OUT"} onChange={(event) => setKind(punchKindSchema.parse(event.target.value))}>
          {["IN", "OUT", "BREAK_START", "BREAK_END"].map((value) => <option key={value} value={value}>{value.replace("_", " ")}</option>)}
        </select></label>
      ) : null}
      {action === "MODIFY_PUNCH" || action === "REMOVE_PUNCH" ? (
        <label><span>Punch ID</span><input aria-label="Punch ID" inputMode="numeric" value={punchId} onChange={(event) => setPunchId(event.target.value)} /></label>
      ) : null}
      {action === "ADJUST_SCHEDULE" ? (
        <label><span>Schedule ID</span><input aria-label="Schedule ID" inputMode="numeric" value={scheduleId} onChange={(event) => setScheduleId(event.target.value)} /></label>
      ) : null}
      {actionHasTime ? (
        <label>
          <span>Correction time</span>
          <input
            aria-label="Correction time"
            type="datetime-local"
            value={ts}
            min={exception.constraints.ts_window?.earliest}
            max={exception.constraints.ts_window?.latest}
            onChange={(event) => setTs(event.target.value)}
            required
          />
          {exception.constraints.ts_window ? <small>Allowed window: {exception.constraints.ts_window.earliest.replace("T", " ")} to {exception.constraints.ts_window.latest.replace("T", " ")}</small> : null}
        </label>
      ) : null}
      {formError ? <p className="field-error" role="alert">{formError}</p> : null}
      {apiError ? <ErrorBanner error={apiError} /> : null}
      <div className="form-actions">
        {onCancel ? <button type="button" className="button button-quiet" onClick={onCancel}>Cancel</button> : null}
        <button type="submit" className="button button-primary" disabled={saving || !editableActions.length}>{saving ? "Submitting…" : "Submit edit"}</button>
      </div>
    </form>
  );
}
