import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch, ApiError } from "../api/client";
import { auditEventSchema, healthSchema, proposalOutSchema } from "../api/schemas";
import { DecisionPanel } from "../components/DecisionPanel";
import { EditCorrectionForm } from "../components/EditCorrectionForm";
import { ExceptionTable } from "../components/ExceptionTable";
import { ManagerSwitcher } from "../components/ManagerSwitcher";
import { ProposalCard, ValidationChecklist } from "../components/ProposalCard";
import { ErrorBanner, SeverityBadge } from "../components/common";
import { ManagerProvider } from "../context/ManagerContext";
import { exceptionSample, fallbackProposal } from "./samples";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("manager review components", () => {
  it("ExceptionTable renders employee rows and review links", () => {
    render(<MemoryRouter><ExceptionTable exceptions={[exceptionSample]} /></MemoryRouter>);
    expect(screen.getByText("Priya Raman")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review" })).toHaveAttribute("href", "/exceptions/14");
  });

  it("SeverityBadge shows text and a non-color marker", () => {
    render(<SeverityBadge severity="high" />);
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("▲")).toBeInTheDocument();
  });

  it("ValidationChecklist presents pass, fail, and warning states", () => {
    render(<ValidationChecklist validation={{
      passed: false,
      checks: [
        { id: "V-01", name: "Schema valid", passed: true, level: "error", detail: "Valid JSON." },
        { id: "V-02", name: "Action allowed", passed: false, level: "error", detail: "Action not permitted." },
        { id: "V-03", name: "Evidence present", passed: true, level: "warning", detail: "Reviewed with caution." },
      ],
      side_effects: [],
    }} />);
    expect(screen.getByText("Schema valid")).toBeInTheDocument();
    expect(screen.getByText("Action allowed")).toBeInTheDocument();
    expect(screen.getByText("Evidence present")).toBeInTheDocument();
    expect(screen.getByText("V-10")).toBeInTheDocument();
  });

  it("ProposalCard identifies a fallback and disables approval for ESCALATE", () => {
    render(<ProposalCard exception={exceptionSample} proposal={fallbackProposal} onApprove={vi.fn()} />);
    expect(screen.getByText("Fallback: escalated, no AI suggestion")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve suggestion" })).toBeDisabled();
  });

  it("EditCorrectionForm constrains time and shows server validation checks", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockRejectedValue(new ApiError(422, "Correction did not pass validation.", "validation_failed", [
      { id: "V-04", name: "Allowed time window", passed: false, level: "error", detail: "The time falls outside the safe window." },
    ]));
    render(<EditCorrectionForm
      exception={exceptionSample}
      initialCorrection={{ action: "ADD_PUNCH", kind: "OUT", ts: "2025-03-12T17:00" }}
      onSubmit={onSubmit}
    />);
    const timeInput = screen.getByLabelText("Correction time");
    expect(timeInput).toHaveAttribute("min", "2025-03-12T12:31");
    expect(timeInput).toHaveAttribute("max", "2025-03-12T20:00");
    await user.click(screen.getByRole("button", { name: "Submit edit" }));
    expect(await screen.findByText("The time falls outside the safe window.")).toBeInTheDocument();
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("DecisionPanel requires a note before rejecting", async () => {
    const user = userEvent.setup();
    const onDecision = vi.fn();
    render(<DecisionPanel exception={exceptionSample} proposal={fallbackProposal} onDecision={onDecision} />);
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Confirm rejection" }));
    expect(await screen.findByText("A note is required when rejecting this proposal.")).toBeInTheDocument();
    expect(onDecision).not.toHaveBeenCalled();
  });

  it("malformed proposal responses are rejected and surfaced in an error banner", async () => {
    const malformed = { ...fallbackProposal, proposal: { ...fallbackProposal.proposal, confidence: 3 } };
    expect(proposalOutSchema.safeParse(malformed).success).toBe(false);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify(malformed),
    }));
    let caught: unknown;
    try {
      await apiFetch("/api/v1/test", {}, proposalOutSchema);
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(ApiError);
    render(<ErrorBanner error={caught} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/response format/i);
  });

  it("accepts UTC audit event timestamps returned by the API", () => {
    expect(auditEventSchema.safeParse({
      id: 1,
      trace_id: "trace-1",
      exception_id: 14,
      seq: 1,
      event_type: "EXCEPTION_DETECTED",
      payload: {},
      prev_hash: "GENESIS",
      hash: "abc123",
      created_at: "2026-11-03T16:56:27.660Z",
    }).success).toBe(true);
  });

  it("ManagerSwitcher changes the X-Manager-Id header used by requests", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ status: "ok", provider: "mock", model: "mock", llm_reachable: true, now: "2025-03-13T20:00" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ManagerProvider><ManagerSwitcher /></ManagerProvider>);
    await user.selectOptions(screen.getByRole("combobox", { name: "Manager" }), "m-002");
    await apiFetch("/api/v1/health", {}, healthSchema);
    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(new Headers(init.headers).get("X-Manager-Id")).toBe("m-002");
  });
});
