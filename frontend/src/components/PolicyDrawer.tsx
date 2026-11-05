import { useEffect, useRef, useState, type FormEvent } from "react";
import type {
  OrganizationPolicy,
  OrganizationPolicyCreate,
  OrganizationPolicyUpdate,
  Severity,
} from "../api/schemas";
import { ErrorBanner } from "./common";
import { IconInfo, IconX } from "./icons";

const SEVERITIES: Severity[] = ["low", "medium", "high", "critical"];
const TEXT_LIMIT = 12000;

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(date);
}

/** Slide-over editor for a built-in rule or a company policy. */
export function PolicyDrawer({
  policy,
  rules,
  busy,
  error,
  onCancel,
  onSave,
}: {
  policy?: OrganizationPolicy;
  rules: OrganizationPolicy[];
  busy: boolean;
  error?: unknown;
  onCancel: () => void;
  onSave: (body: OrganizationPolicyCreate | OrganizationPolicyUpdate) => Promise<void>;
}) {
  const [title, setTitle] = useState(policy?.title ?? "");
  const [text, setText] = useState(policy?.text ?? "");
  const [severity, setSeverity] = useState<Severity>(policy?.severity ?? "medium");
  const [active, setActive] = useState(policy?.is_active ?? true);
  const [appliesTo, setAppliesTo] = useState<string[]>(policy?.applies_to_rule_ids ?? []);
  const isStandard = policy?.kind === "standard";
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    titleRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);

  function toggleRule(ruleId: string) {
    setAppliesTo((current) => current.includes(ruleId)
      ? current.filter((id) => id !== ruleId)
      : [...current, ruleId]);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      if (policy?.kind === "standard") {
        await onSave({ title, text, severity, is_active: active });
      } else if (policy) {
        await onSave({ title, text, is_active: active, applies_to_rule_ids: appliesTo });
      } else {
        await onSave({ title, text, applies_to_rule_ids: appliesTo });
      }
    } catch {
      // The owning page renders the mutation's API error inside the drawer.
    }
  }

  return (
    <div
      className="drawer-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <aside className="policy-drawer" role="dialog" aria-modal="true" aria-labelledby="policy-editor-title">
        <header className="policy-drawer-header">
          <div>
            <p className="eyebrow">{policy ? (isStandard ? "Built-in rule" : "Company policy") : "New company policy"}</p>
            <h2 id="policy-editor-title">{policy ? (isStandard ? `Edit ${policy.rule_id ?? "rule"}` : "Edit company policy") : "Add a company policy"}</h2>
            {policy ? <p className="policy-drawer-meta">Revision {policy.revision} · Updated {formatDate(policy.updated_at)}</p> : null}
          </div>
          <button type="button" className="icon-button" aria-label="Close policy editor" onClick={onCancel}><IconX /></button>
        </header>

        <form id="policy-form" className="policy-drawer-body" onSubmit={(event) => void submit(event)}>
          {error ? <ErrorBanner error={error} /> : null}
          {isStandard ? (
            <p className="policy-note"><IconInfo /> Detection thresholds and logic are fixed. You can change the wording, the severity, and whether this rule is enabled.</p>
          ) : null}

          <label className="field">
            <span>Title</span>
            <input ref={titleRef} required minLength={3} maxLength={160} value={title} onChange={(event) => setTitle(event.target.value)} />
          </label>

          <label className="field">
            <span>{isStandard ? "Description" : "Policy guidance"}</span>
            <textarea required minLength={20} maxLength={TEXT_LIMIT} rows={8} value={text} onChange={(event) => setText(event.target.value)} />
            <small>
              <span>Clear, specific wording works best. The AI reads this as policy context.</span>
              <span className="field-count">{text.length}/{TEXT_LIMIT}</span>
            </small>
          </label>

          {isStandard ? (
            <fieldset className="segmented-field">
              <legend>Severity</legend>
              <div className="segmented" role="radiogroup" aria-label="Severity">
                {SEVERITIES.map((value) => (
                  <label key={value}>
                    <input type="radio" name="severity" value={value} checked={severity === value} onChange={() => setSeverity(value)} />
                    <span><i className={`sev-dot sev-${value}`} aria-hidden="true" />{value}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          ) : (
            <fieldset className="rule-picker-field">
              <legend>Applies to rules</legend>
              <p>Pick the built-in rules this guidance relates to, or leave all unchecked for a general attendance policy.</p>
              <div className="rule-picker">
                {rules.map((rule) => (
                  <label key={rule.policy_id}>
                    <input type="checkbox" checked={appliesTo.includes(rule.policy_id)} onChange={() => toggleRule(rule.policy_id)} />
                    <span><b>{rule.policy_id}</b> {rule.title}</span>
                  </label>
                ))}
              </div>
              {appliesTo.length ? <button type="button" className="link-button" onClick={() => setAppliesTo([])}>Clear selection ({appliesTo.length})</button> : null}
            </fieldset>
          )}

          {policy ? (
            <label className="switch">
              <input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} />
              <span className="switch-track" aria-hidden="true" />
              <span className="switch-label">{isStandard ? "Rule is enabled for automated detection" : "Policy is active for AI retrieval"}</span>
            </label>
          ) : null}
        </form>

        <footer className="policy-drawer-footer">
          <button type="button" className="button button-outline" onClick={onCancel}>Cancel</button>
          <button type="submit" form="policy-form" className="button button-primary" disabled={busy}>{busy ? "Saving…" : "Save policy"}</button>
        </footer>
      </aside>
    </div>
  );
}
