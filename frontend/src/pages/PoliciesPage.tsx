import { useState, type FormEvent } from "react";
import {
  useArchiveOrganizationPolicy,
  useCreateOrganizationPolicy,
  useOrganizationPolicies,
  useUpdateOrganizationPolicy,
} from "../api/hooks";
import type {
  OrganizationPolicy,
  OrganizationPolicyCreate,
  OrganizationPolicyUpdate,
  Severity,
} from "../api/schemas";
import { EmptyState, ErrorBanner, LoadingState, PageHeading } from "../components/common";

const severityOptions: Severity[] = ["low", "medium", "high", "critical"];

export function PoliciesPage() {
  const policies = useOrganizationPolicies();
  const create = useCreateOrganizationPolicy();
  const update = useUpdateOrganizationPolicy();
  const archive = useArchiveOrganizationPolicy();
  const [editing, setEditing] = useState<OrganizationPolicy | "new" | null>(null);

  if (policies.isLoading) return <main className="page-shell"><LoadingState label="Loading company policies…" /></main>;
  if (policies.isError || !policies.data) {
    return <main className="page-shell"><PageHeading eyebrow="HR configuration" title="Policies" /><ErrorBanner error={policies.error} onRetry={() => void policies.refetch()} /></main>;
  }

  const standards = policies.data.filter((policy) => policy.kind === "standard");
  const companyPolicies = policies.data.filter((policy) => policy.kind === "custom");
  const activeCompanyPolicies = companyPolicies.filter((policy) => policy.is_active);
  const archivedCompanyPolicies = companyPolicies.filter((policy) => !policy.is_active);
  const editorError = create.error ?? update.error;

  async function savePolicy(input: { policy: OrganizationPolicy | "new"; body: OrganizationPolicyCreate | OrganizationPolicyUpdate }) {
    if (input.policy === "new") {
      await create.mutateAsync(input.body as OrganizationPolicyCreate);
    } else {
      await update.mutateAsync({
        policyId: input.policy.policy_id,
        body: input.body as OrganizationPolicyUpdate,
      });
    }
    setEditing(null);
  }

  function archivePolicy(policy: OrganizationPolicy) {
    if (window.confirm(`Archive “${policy.title}”? It will remain in history but no longer be used by the assistant.`)) {
      archive.mutate(policy.policy_id);
    }
  }

  return (
    <main className="page-shell policies-page">
      <PageHeading eyebrow="HR configuration" title="Attendance policies">
        <button type="button" className="button button-primary" onClick={() => setEditing("new")}>
          + Add company policy
        </button>
      </PageHeading>

      <section className="policy-explainer" aria-label="Policy behavior">
        <strong>How policies are used</strong>
        <p>Enabling a built-in rule and changing its severity affect detection. Edited wording and company policies provide context to analysis and Ask ShiftGuard. Built-in thresholds and detector logic stay fixed; added guidance does not create a new automated detector.</p>
      </section>

      {editorError ? <ErrorBanner error={editorError} /> : null}
      {archive.error ? <ErrorBanner error={archive.error} /> : null}
      {editing ? (
        <PolicyEditor
          key={editing === "new" ? "new-policy" : editing.policy_id}
          policy={editing === "new" ? undefined : editing}
          rules={standards}
          busy={create.isPending || update.isPending}
          onCancel={() => setEditing(null)}
          onSave={(body) => savePolicy({ policy: editing, body })}
        />
      ) : null}

      <section className="policy-section" aria-labelledby="implemented-rules-heading">
        <div className="policy-section-heading">
          <div><p className="eyebrow">15 built-in rules</p><h2 id="implemented-rules-heading">Implemented attendance rules</h2></div>
          <span className="count-chip">{standards.filter((rule) => rule.is_active).length} active</span>
        </div>
        <div className="organization-policy-grid">
          {standards.map((policy) => (
            <PolicyCard key={policy.policy_id} policy={policy} onEdit={() => setEditing(policy)} />
          ))}
        </div>
      </section>

      <section className="policy-section" aria-labelledby="company-policies-heading">
        <div className="policy-section-heading">
          <div><p className="eyebrow">AI reference material</p><h2 id="company-policies-heading">Company policies</h2></div>
          <span className="count-chip">{activeCompanyPolicies.length} active</span>
        </div>
        {activeCompanyPolicies.length ? (
          <div className="organization-policy-grid">
            {activeCompanyPolicies.map((policy) => (
              <PolicyCard key={policy.policy_id} policy={policy} onEdit={() => setEditing(policy)} onArchive={() => archivePolicy(policy)} />
            ))}
          </div>
        ) : (
          <EmptyState title="No company policies added" detail="Add local guidance to give the analyzer and Ask ShiftGuard organization-specific context." />
        )}
        {archivedCompanyPolicies.length ? (
          <details className="archived-policies">
            <summary>Archived policies ({archivedCompanyPolicies.length})</summary>
            <div className="organization-policy-grid">
              {archivedCompanyPolicies.map((policy) => (
                <PolicyCard key={policy.policy_id} policy={policy} onEdit={() => setEditing(policy)} />
              ))}
            </div>
          </details>
        ) : null}
      </section>
    </main>
  );
}

function PolicyCard({ policy, onEdit, onArchive }: {
  policy: OrganizationPolicy;
  onEdit: () => void;
  onArchive?: () => void;
}) {
  const active = policy.is_active;
  return (
    <article className={`organization-policy-card ${active ? "" : "is-inactive"}`} id={`policy-${policy.policy_id}`}>
      <div className="organization-policy-card-heading">
        <div>
          <span className="rule-code">{policy.rule_id ?? "COMPANY POLICY"}</span>
          <span className={`policy-state ${active ? "is-active" : ""}`}>{active ? "Active" : policy.kind === "custom" ? "Archived" : "Disabled"}</span>
        </div>
        <span className="policy-revision">r{policy.revision}</span>
      </div>
      <h3>{policy.title}</h3>
      <p className="organization-policy-text">{policy.text}</p>
      <div className="organization-policy-meta">
        {policy.severity ? <span className={`severity-badge severity-${policy.severity}`}>{policy.severity} severity</span> : <span className="source-badge source-policy">AI reference</span>}
        {policy.kind === "custom" ? (
          <span>{policy.applies_to_rule_ids.length ? `Applies to ${policy.applies_to_rule_ids.join(", ")}` : "Applies across attendance topics"}</span>
        ) : <span>{policy.category}</span>}
      </div>
      <div className="organization-policy-actions">
        <button type="button" className="button button-outline" onClick={onEdit}>{policy.kind === "custom" && !active ? "Edit / restore" : "Edit"}</button>
        {onArchive ? <button type="button" className="button button-danger-outline" onClick={onArchive}>Archive</button> : null}
      </div>
    </article>
  );
}

function PolicyEditor({ policy, rules, busy, onCancel, onSave }: {
  policy?: OrganizationPolicy;
  rules: OrganizationPolicy[];
  busy: boolean;
  onCancel: () => void;
  onSave: (body: OrganizationPolicyCreate | OrganizationPolicyUpdate) => Promise<void>;
}) {
  const [title, setTitle] = useState(policy?.title ?? "");
  const [text, setText] = useState(policy?.text ?? "");
  const [severity, setSeverity] = useState<Severity>(policy?.severity ?? "medium");
  const [active, setActive] = useState(policy?.is_active ?? true);
  const [appliesTo, setAppliesTo] = useState<string[]>(policy?.applies_to_rule_ids ?? []);
  const isStandard = policy?.kind === "standard";

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
      // The owning page renders the mutation's API error above the editor.
    }
  }

  return (
    <section className="policy-editor panel" aria-labelledby="policy-editor-title">
      <div className="panel-heading"><div><p className="eyebrow">{policy ? "Policy revision" : "New company guidance"}</p><h2 id="policy-editor-title">{policy ? `Edit ${policy.rule_id ?? "company policy"}` : "Add a company policy"}</h2></div><button type="button" className="icon-button" aria-label="Close policy editor" onClick={onCancel}>×</button></div>
      <form className="policy-editor-form" onSubmit={(event) => void submit(event)}>
        <label>Policy title<input required minLength={3} maxLength={160} value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <label>Policy guidance<textarea required minLength={20} maxLength={12000} rows={5} value={text} onChange={(event) => setText(event.target.value)} /><small>Use clear rules and relevant exceptions. This text is supplied to the AI as policy context.</small></label>
        {isStandard ? (
          <label>Severity<select value={severity} onChange={(event) => setSeverity(event.target.value as Severity)}>{severityOptions.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        ) : (
          <fieldset className="policy-rule-picker"><legend>Applies to implemented rules</legend><p>Leave all unchecked to make this a general attendance policy.</p><div>{rules.map((rule) => <label key={rule.policy_id}><input type="checkbox" checked={appliesTo.includes(rule.policy_id)} onChange={() => toggleRule(rule.policy_id)} /><span>{rule.policy_id} · {rule.title}</span></label>)}</div></fieldset>
        )}
        {policy ? <label className="policy-active-toggle"><input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} />{isStandard ? "Rule is enabled for automated detection" : "Policy is active for AI retrieval"}</label> : null}
        <div className="policy-editor-actions"><button type="button" className="button button-outline" onClick={onCancel}>Cancel</button><button type="submit" className="button button-primary" disabled={busy}>{busy ? "Saving…" : "Save policy"}</button></div>
      </form>
    </section>
  );
}
