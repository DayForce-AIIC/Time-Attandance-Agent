# Five-slide presentation outline

## Slide 1 — Problem and user value

- Managers spend payroll-close time reconciling missing punches, overtime risk, schedules, and policy.
- Three costs: slow review, compliance/pay accuracy risk, and inconsistent decisions.
- ShiftGuard pairs evidence and policy with a constrained suggestion and manager decision.
- Show: queue screenshot and the clearly labelled time-saved assumptions (6 manual minutes, 1 assisted review minute); do not present the assumption as measured savings.

## Slide 2 — Workflow and demo

- Show the six-step journey: deterministic detection → case context → validated proposal → human decision → audit event → metrics.
- Walk through Priya Raman's missing OUT punch and Marcus Lee's OT-02 schedule edit.
- Show: queue, case timeline/proposal, decision summary, and Ask ShiftGuard's cited policy answer.

## Slide 3 — Architecture and harness

- Use the diagram in [`docs/ARCHITECTURE.md`](ARCHITECTURE.md).
- Call out H1–H11: trusted context, typed contracts, deterministic logic, bounds, V-01–V-10, simulation, retry/fallback, human decision, audit trace, untrusted notes, independent evaluation.
- Key sentence: the LLM is one component; it cannot decide or write corrections.
- The read-only assistant receives manager-scoped sources; whole-team schedule optimization waits for staffing targets and availability data.
- Show: one V-01–V-10 checklist, the validation/fallback sequence, and the assistant's source cards.

## Slide 4 — Safety, testing, and evaluation

- Show the results in `eval/results.md`: 100% detection recall (46/46), 100% first-pass validation, zero clean false positives, zero unsafe escapes, and fault-mode outcomes.
- State test evidence accurately using the current local suite; separate the mock proposal evaluation from any real-model assistant trial.
- Show: threat-model rows for out-of-bounds time, injection, and overtime side effects; show a successful GitHub Actions run only after one is available.

## Slide 5 — Future work and business impact

- Future work: timekeeping-system integration, broader policy libraries and jurisdictions, real identity and access control, DST/multiple-shift support, and governed learning from manager edits.
- Potential business outcomes: faster exception review, fewer unsupported timecard changes, and a more traceable payroll-close process.
- Keep potential value separate from measured evaluation results.
