import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { z } from "zod";
import type { DecisionOut } from "../api/schemas";
import { punchSchema, scheduleSchema } from "../api/schemas";
import { describeAuditEvent } from "../components/AuditDrawer";
import { DecisionSummary, describeFinalCorrection, reviewerLabel } from "../components/DecisionSummary";
import { EvidencePanel, sourceLabel, summarizeDay } from "../components/EvidencePanel";
import { splitPolicyText } from "../policyText";
import { exceptionSample } from "./samples";

type Punch = z.infer<typeof punchSchema>;
type Schedule = z.infer<typeof scheduleSchema>;

const REVIEWER = "83af5efd-e904-4874-bef5-e50557085140";

const approved: DecisionOut = {
  id: 7,
  exception_id: 14,
  proposal_id: 4,
  action: "approve",
  manager_id: REVIEWER,
  note: null,
  final_correction: { action: "ADD_PUNCH", punch_id: null, kind: "OUT", ts: "2025-03-12T17:00", schedule_id: null, note: null },
  decided_at: "2025-03-13T10:58",
  resulting_status: "RESOLVED",
  writeback_status: "applied",
  external_operation_id: "mock-op-1",
};

function renderSummary(decision: DecisionOut, props: Partial<Parameters<typeof DecisionSummary>[0]> = {}) {
  return render(<MemoryRouter><DecisionSummary decision={decision} {...props} /></MemoryRouter>);
}

describe("decision summary", () => {
  it("explains an approved decision in plain words and hides the technical ids", () => {
    renderSummary(approved, { currentUserId: REVIEWER });
    expect(screen.getByRole("heading", { name: "Correction approved" })).toBeInTheDocument();
    expect(screen.getByText(/Decided by you on/)).toBeInTheDocument();
    expect(screen.getByText("Added a clock-out at 5:00 PM.")).toBeInTheDocument();
    expect(screen.getByText("Saved to the timekeeping system")).toBeInTheDocument();
    expect(screen.getByText("mock-op-1")).not.toBeVisible();
  });

  it("never shows a raw reviewer id in the headline", () => {
    renderSummary(approved, { currentUserId: "someone-else" });
    expect(screen.getByText(/Decided by another HR reviewer on/)).toBeInTheDocument();
  });

  it("offers a retry when saving to the timekeeping system failed", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    renderSummary({ ...approved, writeback_status: "failed" }, { onRetryWriteback: onRetry });
    expect(screen.getByRole("heading", { name: "Approved, but saving failed" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry saving" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("says the change was applied to the timecard when no external system is involved", () => {
    renderSummary({ ...approved, writeback_status: "not_requested" });
    expect(screen.getByText("Applied to the timecard")).toBeInTheDocument();
  });

  it("describes a rejection as no timecard change", () => {
    renderSummary({ ...approved, action: "reject", final_correction: null, writeback_status: "not_requested", note: "Not a real exception" });
    expect(screen.getByRole("heading", { name: "Suggestion rejected" })).toBeInTheDocument();
    expect(screen.getByText("No timecard change was made.")).toBeInTheDocument();
    expect(screen.getByText("Not a real exception")).toBeInTheDocument();
  });

  it("turns ids and corrections into readable text", () => {
    expect(reviewerLabel("m-001")).toBe("Avery Singh");
    expect(reviewerLabel(REVIEWER)).toBe("another HR reviewer");
    expect(describeFinalCorrection({ action: "FLAG_FOR_APPROVAL", punch_id: null, kind: null, ts: null, schedule_id: null, note: null }))
      .toBe("Flagged for approval. No timecard change was needed.");
  });
});

const punches: Punch[] = [
  { id: 1, employee_id: "e-001", work_date: "2025-03-12", ts: "2025-03-12T09:00", kind: "IN", source: "synthetic-device", voided: false },
  { id: 2, employee_id: "e-001", work_date: "2025-03-12", ts: "2025-03-12T12:00", kind: "BREAK_START", source: "synthetic-device", voided: false },
  { id: 3, employee_id: "e-001", work_date: "2025-03-12", ts: "2025-03-12T12:30", kind: "BREAK_END", source: "synthetic-device", voided: false },
  { id: 4, employee_id: "e-001", work_date: "2025-03-12", ts: "2025-03-12T17:00", kind: "OUT", source: "synthetic-device", voided: false },
];
const schedules: Schedule[] = [
  { id: 10, employee_id: "e-001", work_date: "2025-03-12", start: "2025-03-12T09:00", end: "2025-03-12T17:00", planned_break_minutes: 30 },
];

describe("evidence panel", () => {
  it("summarises the day in numbers and lists punches in plain words", () => {
    render(<EvidencePanel exception={exceptionSample} punches={punches} schedules={schedules} />);
    expect(screen.getByText("7h 30m")).toBeInTheDocument();
    // shown once as the scheduled shift and once as the actual first-in to last-out
    expect(screen.getAllByText("9:00 AM – 5:00 PM")).toHaveLength(2);
    expect(screen.getByText("30 min")).toBeInTheDocument();
    for (const label of ["Clock in", "Break start", "Break end", "Clock out"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.queryByText("Active")).not.toBeInTheDocument();
  });

  it("marks a day with no clock-out as incomplete and flags voided punches", () => {
    const withVoid: Punch[] = [
      ...punches.filter((punch) => punch.kind !== "OUT"),
      { id: 9, employee_id: "e-001", work_date: "2025-03-12", ts: "2025-03-12T17:01", kind: "OUT", source: null, voided: true },
    ];
    render(<EvidencePanel exception={exceptionSample} punches={withVoid} schedules={[]} />);
    expect(screen.getByText("Incomplete")).toBeInTheDocument();
    expect(screen.getByText("No schedule")).toBeInTheDocument();
    expect(screen.getByText("Voided")).toBeInTheDocument();
  });

  it("names the punch source in words instead of showing a code", () => {
    render(<EvidencePanel exception={exceptionSample} punches={punches} schedules={schedules} />);
    expect(screen.getAllByText("Time clock (sample data)")).toHaveLength(4);
    expect(sourceLabel("device")).toBe("Time clock");
    expect(sourceLabel(null)).toBe("Not recorded");
  });

  it("computes net worked time as the shift minus the break", () => {
    expect(summarizeDay(punches).worked).toBe(450);
    expect(summarizeDay(punches).breakMinutes).toBe(30);
  });
});

describe("policy wording", () => {
  it("shows the § sign as a section citation kept apart from the rule text", () => {
    expect(splitPolicyText("Time & Attendance Policy §2.9: Punch sequences that cannot be interpreted must be reviewed manually."))
      .toEqual({ source: "Time & Attendance Policy · Section 2.9", body: "Punch sequences that cannot be interpreted must be reviewed manually." });
  });

  it("leaves company policies without a citation unchanged", () => {
    expect(splitPolicyText("No Body Should Punch IN during night times")).toEqual({ source: null, body: "No Body Should Punch IN during night times" });
    expect(splitPolicyText("See §4 for details").body).toBe("See Section 4 for details");
  });
});

describe("audit trail wording", () => {
  it("uses plain labels for known events and readable text for unknown ones", () => {
    expect(describeAuditEvent("DECISION_MADE")).toMatchObject({ label: "Decision recorded", tone: "ok" });
    expect(describeAuditEvent("SOMETHING_NEW").label).toBe("Something new");
  });
});
