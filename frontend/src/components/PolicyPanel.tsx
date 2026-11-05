import type { z } from "zod";
import { policyPassageSchema, type ProposalOut } from "../api/schemas";
import { splitPolicyText } from "../policyText";

type PolicyPassage = z.infer<typeof policyPassageSchema>;

/** Collapsible reference list of the policy passages used for this case. */
export function PolicyPanel({
  policies,
  proposal,
  open,
  onToggle,
}: {
  policies: PolicyPassage[];
  proposal?: ProposalOut | null;
  open?: boolean;
  onToggle?: (open: boolean) => void;
}) {
  const cited = new Set(proposal?.proposal.policy_ids_cited ?? []);
  const showingSnapshot = Boolean(proposal?.policy_snapshot.length);
  const visiblePolicies = showingSnapshot
    ? proposal!.policy_snapshot
    : policies.map((policy) => ({ ...policy, revision: null }));
  return (
    <details className="panel policy-panel" open={open} onToggle={(event) => onToggle?.(event.currentTarget.open)}>
      <summary className="panel-heading">
        <div><p className="eyebrow">Reference</p><h2 id="policy-title">{showingSnapshot ? "Policies used for this suggestion" : "Relevant company policies"}</h2></div>
        <span className="count-chip">{visiblePolicies.length} {visiblePolicies.length === 1 ? "policy" : "policies"}</span>
      </summary>
      <div className="policy-list">
        {visiblePolicies.map((policy) => {
          const isCited = cited.has(policy.policy_id);
          const { source, body } = splitPolicyText(policy.text);
          return (
            <article className={`policy-card ${isCited ? "policy-cited" : ""}`} id={`policy-${policy.policy_id}`} key={policy.policy_id}>
              <div className="policy-title-row">
                <span className="rule-code">{policy.policy_id}</span>
                {isCited ? <span className="cited-label">Used for this suggestion</span> : null}
                {policy.revision !== null ? <span className="policy-revision">Revision {policy.revision}</span> : null}
              </div>
              <h3>{policy.title}</h3>
              {source ? <p className="policy-source">{source}</p> : null}
              <p>{body}</p>
            </article>
          );
        })}
      </div>
    </details>
  );
}
