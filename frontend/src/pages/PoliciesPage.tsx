import { useMemo, useState } from "react";
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
import { BRAND } from "../brand";
import { ErrorBanner, LoadingState, PageHeading, SeverityBadge } from "../components/common";
import { IconArchive, IconEdit, IconInfo, IconPlus, IconRestore, IconSearch } from "../components/icons";
import { PolicyDrawer } from "../components/PolicyDrawer";

type Tab = "rules" | "company";

const CATEGORY_LABELS: Record<string, string> = {
  punch: "Punches",
  break: "Breaks",
  overtime: "Overtime",
  schedule: "Schedule",
  rest: "Rest periods",
  days: "Consecutive days",
  company: "Company",
};
const MAX_SCOPE_CHIPS = 6;

function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category.charAt(0).toUpperCase() + category.slice(1);
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(date);
}

// Same order as the built-in rule book: punches, then breaks, overtime, schedule, rest and days.
const RULE_ORDER = [
  "PUNCH-01", "PUNCH-02", "PUNCH-03", "PUNCH-04", "PUNCH-99", "MAX-01", "BRK-01", "BRK-02",
  "OT-01", "OT-02", "OT-03", "SCHED-01", "SCHED-02", "REST-01", "DAY-01",
];
const ruleRank = (policy: OrganizationPolicy) => {
  const index = RULE_ORDER.indexOf(policy.rule_id ?? policy.policy_id);
  return index === -1 ? RULE_ORDER.length : index;
};
const byRule = (a: OrganizationPolicy, b: OrganizationPolicy) =>
  ruleRank(a) - ruleRank(b)
  || (a.rule_id ?? a.policy_id).localeCompare(b.rule_id ?? b.policy_id, undefined, { numeric: true });

export function PoliciesPage() {
  const policies = useOrganizationPolicies();
  const create = useCreateOrganizationPolicy();
  const update = useUpdateOrganizationPolicy();
  const archive = useArchiveOrganizationPolicy();
  const [editing, setEditing] = useState<OrganizationPolicy | "new" | null>(null);
  const [tab, setTab] = useState<Tab>("rules");
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState<"" | Severity>("");
  const [ruleStatus, setRuleStatus] = useState<"" | "enabled" | "disabled">("");
  const [companyStatus, setCompanyStatus] = useState<"active" | "archived" | "all">("active");

  const data = policies.data;
  const standards = useMemo(() => (data ?? []).filter((policy) => policy.kind === "standard").sort(byRule), [data]);
  const company = useMemo(() => (data ?? []).filter((policy) => policy.kind === "custom"), [data]);

  if (policies.isLoading) return <main className="page-shell"><LoadingState label="Loading company policies…" /></main>;
  if (policies.isError || !data) {
    return <main className="page-shell"><PageHeading eyebrow="HR configuration" title="Policies" /><ErrorBanner error={policies.error} onRetry={() => void policies.refetch()} /></main>;
  }

  const query = search.trim().toLowerCase();
  const matches = (policy: OrganizationPolicy) => !query
    || [policy.title, policy.text, policy.rule_id ?? "", policy.category].some((value) => value.toLowerCase().includes(query));
  const visibleRules = standards.filter((policy) => matches(policy)
    && (!severity || policy.severity === severity)
    && (ruleStatus === "" || (ruleStatus === "enabled") === policy.is_active));
  const visibleCompany = company.filter((policy) => matches(policy)
    && (companyStatus === "all" || (companyStatus === "active") === policy.is_active));

  const editorError = create.error ?? update.error;
  const filtersActive = Boolean(query || severity || ruleStatus);

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

  function clearFilters() {
    setSearch("");
    setSeverity("");
    setRuleStatus("");
  }

  return (
    <main className="page-shell policies-page">
      <PageHeading
        eyebrow="HR configuration"
        title="Attendance policies"
        subtitle="Choose which rules detect exceptions, and add company guidance for the AI analysis and the assistant."
      >
        <button type="button" className="button button-primary" onClick={() => setEditing("new")}><IconPlus /> Add company policy</button>
      </PageHeading>

      <p className="policy-callout">
        <IconInfo />
        <span>Enabling a rule and changing its severity affect detection. Edited wording and company policies give context to analysis and Ask {BRAND.name}. Built-in thresholds and detector logic stay fixed, and added guidance never creates a new automated detector.</span>
      </p>

      {archive.error ? <ErrorBanner error={archive.error} /> : null}

      <section className="panel policy-main" aria-label="Policy library">
        <div className="policy-tabs" role="tablist" aria-label="Policy type">
          <button type="button" role="tab" id="tab-rules" aria-selected={tab === "rules"} aria-controls="panel-rules" className="policy-tab" onClick={() => setTab("rules")}>
            Built-in rules <span className="count-chip">{standards.length}</span>
          </button>
          <button type="button" role="tab" id="tab-company" aria-selected={tab === "company"} aria-controls="panel-company" className="policy-tab" onClick={() => setTab("company")}>
            Company policies <span className="count-chip">{company.length}</span>
          </button>
        </div>

        <div className="policy-toolbar">
          <div className="search-field">
            <IconSearch />
            <input type="search" placeholder={tab === "rules" ? "Search rules by name, code or wording…" : "Search company policies…"} aria-label="Search policies" value={search} onChange={(event) => setSearch(event.target.value)} />
          </div>
          {tab === "rules" ? (
            <>
              <select aria-label="Filter by severity" value={severity} onChange={(event) => setSeverity(event.target.value as "" | Severity)}>
                <option value="">All severities</option>
                <option value="critical">Critical</option>
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>
              <select aria-label="Filter by status" value={ruleStatus} onChange={(event) => setRuleStatus(event.target.value as "" | "enabled" | "disabled")}>
                <option value="">Enabled and disabled</option>
                <option value="enabled">Enabled only</option>
                <option value="disabled">Disabled only</option>
              </select>
            </>
          ) : (
            <select aria-label="Filter company policies" value={companyStatus} onChange={(event) => setCompanyStatus(event.target.value as "active" | "archived" | "all")}>
              <option value="active">Active</option>
              <option value="archived">Archived</option>
              <option value="all">All</option>
            </select>
          )}
          <span className="policy-toolbar-count">
            {tab === "rules" ? `${visibleRules.length} of ${standards.length} rules` : `${visibleCompany.length} of ${company.length} policies`}
          </span>
        </div>

        {tab === "rules" ? (
          <div id="panel-rules" role="tabpanel" aria-labelledby="tab-rules">
            {visibleRules.length ? (
              <div className="table-scroll">
                <table className="data-table policy-table">
                  <thead>
                    <tr>
                      <th scope="col">Rule</th>
                      <th scope="col">Severity</th>
                      <th scope="col">Status</th>
                      <th scope="col">Revision</th>
                      <th scope="col"><span className="visually-hidden">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleRules.map((policy) => (
                      <tr key={policy.policy_id} className={policy.is_active ? "" : "is-disabled"} onClick={() => setEditing(policy)}>
                        <td>
                          <div className="policy-rule-cell">
                            <span className="rule-code">{policy.rule_id}</span>
                            <div className="policy-rule-body">
                              <div className="policy-rule-title">
                                <strong>{policy.title}</strong>
                                <span className="category-tag">{categoryLabel(policy.category)}</span>
                              </div>
                              <p className="policy-rule-text">{policy.text}</p>
                            </div>
                          </div>
                        </td>
                        <td>{policy.severity ? <SeverityBadge severity={policy.severity} /> : "—"}</td>
                        <td><span className={`policy-state ${policy.is_active ? "is-active" : ""}`}>{policy.is_active ? "Enabled" : "Disabled"}</span></td>
                        <td className="mono">r{policy.revision}</td>
                        <td className="cell-action">
                          <button type="button" className="button button-outline" onClick={(event) => { event.stopPropagation(); setEditing(policy); }}><IconEdit /> Edit</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty-state">
                <div className="empty-mark" aria-hidden="true">?</div>
                <h2>No rules match your filters</h2>
                <p>Try a different search, or clear the filters to see every rule.</p>
                {filtersActive ? <button type="button" className="button button-outline" onClick={clearFilters}>Clear filters</button> : null}
              </div>
            )}
          </div>
        ) : (
          <div id="panel-company" role="tabpanel" aria-labelledby="tab-company">
            {visibleCompany.length ? (
              <div className="company-grid">
                {visibleCompany.map((policy) => (
                  <article className={`company-policy${policy.is_active ? "" : " is-archived"}`} key={policy.policy_id} id={`policy-${policy.policy_id}`}>
                    <header>
                      <h3>{policy.title}</h3>
                      <span className={`policy-state ${policy.is_active ? "is-active" : ""}`}>{policy.is_active ? "Active" : "Archived"}</span>
                    </header>
                    <p className="company-policy-text">{policy.text}</p>
                    <div className="company-policy-scope">
                      <span>Applies to</span>
                      {policy.applies_to_rule_ids.length ? (
                        <>
                          {policy.applies_to_rule_ids.slice(0, MAX_SCOPE_CHIPS).map((ruleId) => <span className="rule-chip" key={ruleId}>{ruleId}</span>)}
                          {policy.applies_to_rule_ids.length > MAX_SCOPE_CHIPS
                            ? <span className="rule-chip is-general">+{policy.applies_to_rule_ids.length - MAX_SCOPE_CHIPS} more</span>
                            : null}
                        </>
                      ) : <span className="rule-chip is-general">All attendance topics</span>}
                    </div>
                    <footer>
                      <small>Updated {formatDate(policy.updated_at)} · r{policy.revision}</small>
                      <div className="company-policy-actions">
                        <button type="button" className="button button-outline" onClick={() => setEditing(policy)}>
                          {policy.is_active ? <><IconEdit /> Edit</> : <><IconRestore /> Edit / restore</>}
                        </button>
                        {policy.is_active ? (
                          <button type="button" className="button button-danger-outline" onClick={() => archivePolicy(policy)}><IconArchive /> Archive</button>
                        ) : null}
                      </div>
                    </footer>
                  </article>
                ))}
              </div>
            ) : (
              <div className="empty-state">
                <div className="empty-mark" aria-hidden="true">+</div>
                <h2>{company.length ? "No policies in this view" : "No company policies yet"}</h2>
                <p>{company.length
                  ? "Switch the filter above to see active or archived policies."
                  : `Add local guidance to give the analyzer and Ask ${BRAND.name} organization-specific context.`}</p>
                {!company.length ? <button type="button" className="button button-primary" onClick={() => setEditing("new")}><IconPlus /> Add company policy</button> : null}
              </div>
            )}
          </div>
        )}
      </section>

      {editing ? (
        <PolicyDrawer
          key={editing === "new" ? "new-policy" : editing.policy_id}
          policy={editing === "new" ? undefined : editing}
          rules={standards}
          busy={create.isPending || update.isPending}
          error={editorError}
          onCancel={() => setEditing(null)}
          onSave={(body) => savePolicy({ policy: editing, body })}
        />
      ) : null}
    </main>
  );
}
