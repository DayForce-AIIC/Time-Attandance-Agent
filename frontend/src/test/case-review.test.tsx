import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { DecisionPanel } from "../components/DecisionPanel";
import { ProposalCard, ValidationChecklist } from "../components/ProposalCard";
import { exceptionSample, fallbackProposal } from "./samples";

function renderWithAuth(ui: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{ui}</AuthProvider>
    </QueryClientProvider>,
  );
}

const addPunchProposal = {
  ...fallbackProposal,
  source: "llm" as const,
  proposal: {
    ...fallbackProposal.proposal,
    explanation: "The employee clocked in and took a break but never clocked out.",
    confidence: 0.92,
    proposed_correction: { action: "ADD_PUNCH" as const, punch_id: null, kind: "OUT" as const, ts: "2025-03-12T17:00", schedule_id: null, note: null },
  },
};

describe("case review", () => {
  it("summarises safety checks in plain words and lists them behind a toggle", () => {
    const checks = Array.from({ length: 10 }, (_, index) => ({
      id: `V-${String(index + 1).padStart(2, "0")}`, name: `Check ${index + 1}`, passed: true, level: "error" as const, detail: "ok",
    }));
    render(<ValidationChecklist validation={{ passed: true, checks, side_effects: [] }} />);
    expect(screen.getByText("All 10 checks passed")).toBeInTheDocument();
    expect(screen.getByText("Check 3")).toBeInTheDocument();
  });

  it("says when there was no AI suggestion to check instead of showing a pass", () => {
    render(<ValidationChecklist validation={fallbackProposal.validation} />);
    expect(screen.getByText("No AI suggestion to check")).toBeInTheDocument();
  });

  it("writes the recommendation as a plain sentence and hides actions when asked", () => {
    renderWithAuth(<ProposalCard exception={exceptionSample} proposal={addPunchProposal} showActions={false} />);
    expect(screen.getByRole("heading", { name: /Add a clock-out at/ })).toBeInTheDocument();
    expect(screen.getByText("92%")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve suggestion" })).not.toBeInTheDocument();
  });

  it("does not show a confidence score for an escalated case", () => {
    renderWithAuth(<ProposalCard exception={exceptionSample} proposal={fallbackProposal} showActions={false} />);
    expect(screen.getByText("No automatic fix: your review is needed")).toBeInTheDocument();
    expect(screen.queryByText("Confidence")).not.toBeInTheDocument();
  });

  it("labels an AI suggestion clearly so a manager knows a model drafted it", () => {
    renderWithAuth(<ProposalCard exception={exceptionSample} proposal={addPunchProposal} showActions={false} />);
    expect(screen.getByText("AI-assisted suggestion")).toBeInTheDocument();
    expect(screen.getByText(/A person makes the final decision/)).toBeInTheDocument();
  });

  it("does not present a fallback as an AI suggestion", () => {
    renderWithAuth(<ProposalCard exception={exceptionSample} proposal={fallbackProposal} showActions={false} />);
    expect(screen.queryByText("AI-assisted suggestion")).not.toBeInTheDocument();
    expect(screen.getByText(/could not produce a safe correction/)).toBeInTheDocument();
  });

  it("lets the reviewer approve a normal suggestion from the decision panel", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn().mockResolvedValue(undefined);
    renderWithAuth(<DecisionPanel exception={exceptionSample} proposal={addPunchProposal} onDecision={vi.fn()} onApprove={onApprove} />);
    await user.click(screen.getByRole("button", { name: "Approve suggestion" }));
    expect(onApprove).toHaveBeenCalledOnce();
  });

  it("offers Edit correction instead of a dead Approve button for an escalated case", () => {
    renderWithAuth(<DecisionPanel exception={exceptionSample} proposal={fallbackProposal} onDecision={vi.fn()} onApprove={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Approve suggestion" })).not.toBeInTheDocument();
    expect(screen.getByText(/no automatic correction to approve/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit correction" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
  });

  it("hides Edit correction when the rule only allows escalation, so nothing looks clickable but useless", () => {
    const escalationOnly = { ...exceptionSample, constraints: { ...exceptionSample.constraints, allowed_actions: ["ESCALATE" as const] } };
    renderWithAuth(<DecisionPanel exception={escalationOnly} proposal={fallbackProposal} onDecision={vi.fn()} onApprove={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Edit correction" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject suggestion" })).toBeInTheDocument();
  });

  it("explains in the recommendation that company policy blocks an automatic fix", () => {
    const escalationOnly = { ...exceptionSample, constraints: { ...exceptionSample.constraints, allowed_actions: ["ESCALATE" as const] } };
    renderWithAuth(<ProposalCard exception={escalationOnly} proposal={fallbackProposal} showActions={false} />);
    expect(screen.getByText(/Company policy doesn’t allow an automatic correction/)).toBeInTheDocument();
    expect(screen.queryByText(/edit the correction yourself/)).not.toBeInTheDocument();
  });
});
