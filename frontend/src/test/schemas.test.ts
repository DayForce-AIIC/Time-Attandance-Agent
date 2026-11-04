import { describe, expect, it } from "vitest";
import { llmProposalSchema } from "../api/schemas";

const proposal = {
  exception_id: 1,
  explanation: "A persisted proposal remains available for HR review after a browser-safe ID update.",
  proposed_correction: { action: "ESCALATE" },
  policy_ids_cited: ["PUNCH-01"],
  confidence: 0,
};

describe("llmProposalSchema", () => {
  it("accepts legacy integer correlation values without using them as workflow IDs", () => {
    const payload = JSON.parse(
      '{"exception_id":188628720161934813,"explanation":"A persisted proposal remains available for HR review after a browser-safe ID update.","proposed_correction":{"action":"ESCALATE"},"policy_ids_cited":["PUNCH-01"],"confidence":0}',
    );

    expect(llmProposalSchema.safeParse(payload).success).toBe(true);
  });

  it("continues to reject a non-integer correlation value", () => {
    expect(llmProposalSchema.safeParse({ ...proposal, exception_id: 1.5 }).success).toBe(false);
  });
});
