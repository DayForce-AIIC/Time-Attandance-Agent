# Ten-minute demo script

## Before recording

1. Run `make setup` once. In separate terminals, run `make backend` and `make frontend`.
2. Open `http://localhost:5173`. Select **Reset demo data** and confirm so the seeded cases are restored. This destructive action is limited to the local demo database.
3. Set **Manager** to Avery Singh and **LLM fault mode** to **No fault**.
4. Keep the API docs at `http://localhost:8000/docs` available only if an endpoint needs explaining; the main story is shown in the UI.

## Script

| Time | Scene | Clicks and narration |
|---|---|---|
| 0:00–0:45 | Pitch | Say: “Timecard exceptions take managers away from payroll close. ShiftGuard uses deterministic detection, lets an LLM make a bounded suggestion, validates it in code, and keeps the manager responsible for the decision.” Show the architecture slide. |
| 0:45–2:00 | Queue | Confirm **Avery Singh** is selected. Point out the nine seeded cases and severity/date sorting. Explain detection runs from the rules engine before an LLM is called. |
| 2:00–4:00 | Missing OUT punch | Find **Priya Raman** (`e-001`), rule **PUNCH-01**, and click **Review**. Point to the missing OUT marker, allowed window, source punches, and policy text. Click **Generate AI proposal**. Walk through the explanation, confidence, cited policies, and safety checklist. Click **Approve suggestion**. Show the decision summary and the added punch with source `correction`. |
| 4:00–5:30 | Weekly overtime risk | In the queue, find **Marcus Lee** (`e-002`), rule **OT-02**, then click **Review**. Generate the proposal and show that the suggested Friday schedule end is 12:30. Click **Edit correction** and try `2025-03-14 12:00`; the field's minimum time is 12:30, so the browser blocks submission and no timecard data changes. Set it to `2025-03-14 12:30` and click **Submit edit**. This edit decision is revalidated and applied immediately; the current UI does not have a second Approve click after a valid edit. Mention that `test_invalid_edit_is_write_free_and_valid_edit_is_recorded` exercises the rejected server-side edit and verifies no write. |
| 5:30–7:30 | Simulation blocks a risky answer; retry recovers | Set **Manager** to Jordan Blake. Set **LLM fault mode** to **Naive latest time**. Find **Raj Patel** (`e-017`), rule **PUNCH-01**, and click **Review** → **Generate AI proposal**. Show the fallback card. Open **Audit trail**, expand the attempt-1 **validation result** payload, and point to V-07's new overtime finding; expand the fallback payload to show the second failure. Close the drawer. Set **LLM fault mode** to **Bad, then recover**. Open **Ben Carter** (`e-014`), rule **PUNCH-01**, and generate. Show “Passed on attempt 2” and the audit `RETRY` event. |
| 7:30–8:15 | Prompt injection stays untrusted | On Ben Carter's case, open **Audit trail** and expand the **prompt built** payload. Show the employee note inside `<untrusted_employee_note>` delimiters. Close the drawer and point to the clean proposal and the passing V-10 check. Explain that the note is context, not an instruction. |
| 8:15–9:00 | Traceability | Switch back to **Avery Singh**, set the queue filter to **All statuses**, and reopen Priya Raman's resolved PUNCH-01 case. Open **Audit trail** and point out the prompt, LLM call, validation, proposal, decision, and correction events. Click **Verify chain** and show **Chain intact**. Note that verification covers the complete event chain. |
| 9:00–10:00 | Evidence and assistant | Open **Ask ShiftGuard** and ask “What policy applies to missed punches?” Show the policy and case source cards; explain the assistant is manager-scoped and read-only. Open **Metrics**, show the safety measures, and state the 6-minute manual and 1-minute assisted assumptions as estimates. Show [`eval/results.md`](../eval/results.md) for the 48-case proposal results. If a GitHub Actions run is available, show it; otherwise describe the configured CI jobs without claiming a hosted run has passed. Close with future work from slide 5. |

## Fault-mode quick reference

The manager header exposes these seven selector options: **No fault**, **Bad, then recover**, **Always invalid**, **Malformed output**, **Timeout**, **Bad citation**, and **Naive latest time**. The underlying mode values are `none`, `bad_then_good`, `always_bad`, `garbage`, `timeout`, `bad_citation`, and `naive_latest`.

`always_bad`, `garbage`, `timeout`, and `bad_citation` end in an escalation after two attempts. `bad_then_good` recovers on the second attempt. `naive_latest` is designed to demonstrate V-07 on e-017's overtime-sensitive missing-OUT case. Reset the local demo data before repeating a mode on a case that already has a proposal or decision.
