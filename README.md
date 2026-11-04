# Time-Attandance-Agent

## Evaluation

The deterministic evaluation harness runs 48 labelled timecard cases through the real detection and proposal pipeline. The independent oracle checks allowed actions, citation validity, safe time windows, and weekly work-hour side effects. Mock-provider results:

| Metric | Result |
|---|---:|
| Detection recall | 100.0% (46/46) |
| Clean false positives | 0 |
| First-pass validation | 100.0% |
| Final LLM pass | 100.0% |
| Escalation rate | 0.0% |
| Correct citation | 100.0% (46/46) |
| In-window timestamps | 100.0% (27/27) |
| Unsafe proposal escapes | 0 |
| Adversarial block/ignore | 100.0% (3/3) |
| Proposal latency p50 / p95 | 10 ms / 10 ms |

All required fault gates passed. `garbage`, `timeout`, `bad_citation`, and `always_bad` each escalated all 10 cases; `bad_then_good` recovered on attempt 2 in all 10. `naive_latest` escalated 2/10 cases, with no unsafe proposal escape.

Run the harness offline with `python eval/run_eval.py`. To evaluate Ollama, start the service, pull the configured model, then run `LLM_PROVIDER=ollama python eval/run_eval.py`. Review `eval/results.md` and paste the real-model numbers here after that run.
