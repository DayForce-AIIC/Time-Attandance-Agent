import type { z } from "zod";
import { policyPassageSchema, type ProposalOut } from "../api/schemas";

type PolicyPassage = z.infer<typeof policyPassageSchema>;

export function PolicyPanel({
  policies,
  proposal,
}: {
  policies: PolicyPassage[];
  proposal?: ProposalOut | null;
}) {
  const cited = new Set(proposal?.proposal.policy_ids_cited ?? []);
  const showingSnapshot = Boolean(proposal?.policy_snapshot.length);
  const visiblePolicies = showingSnapshot
    ? proposal!.policy_snapshot
    : policies.map((policy) => ({ ...policy, revision: null }));
  return (
    <section className="panel policy-panel" aria-labelledby="policy-title">
      <div className="panel-heading">
        <div><p className="eyebrow">Reference</p><h2 id="policy-title">{showingSnapshot ? "Policies used for proposal" : "Retrieved policies"}</h2></div>
        <span className="count-chip">{visiblePolicies.length} passages</span>
      </div>
      <div className="policy-list">
        {visiblePolicies.map((policy) => {
          const isCited = cited.has(policy.policy_id);
          return (
            <article className={`policy-card ${isCited ? "policy-cited" : ""}`} id={`policy-${policy.policy_id}`} key={policy.policy_id}>
              <div className="policy-title-row">
                <span className="rule-code">{policy.policy_id}</span>
                {isCited ? <span className="cited-label">Cited in proposal</span> : null}
                {policy.revision !== null ? <span className="policy-revision">Revision {policy.revision} used</span> : null}
              </div>
              <h3>{policy.title}</h3>
              <p>{policy.text}</p>
            </article>
          );
        })}
      </div>
    </section>
  );
}
