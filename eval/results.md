# Evaluation results

Provider/model: `mock` / `mock`. Mode: `with-fault-injection`. Cases: 48 total; 46 expected findings.

Rates use expected finding cases unless noted. First-pass is the attempt-1 pipeline validation result. In-window uses proposals with a timestamp and a defined safe window. Latency uses final pipeline-reported proposal latency.

| Metric | Result |
|---|---:|
| Detection recall | 100.0% (46/46) |
| Clean false positives | 0 |
| First-pass validation | 100.0% |
| Final LLM pass | 100.0% |
| Safe fallback rate | 0.0% (0/46) |
| Escalation rate | 0.0% |
| Correct citation | 100.0% (46/46) |
| Independent oracle pass | 100.0% (46/46) |
| In-window timestamps | 100.0% (27/27) |
| Unsafe proposal escapes | 0 (0.0%) |
| Adversarial block/ignore | 100.0% (3/3) |
| Proposal latency p50 / p95 | 10 ms / 10 ms |

## Fault injection

| Mode | Fallback | Retry recovered | Unsafe escaped |
|---|---:|---:|---:|
| `garbage` | 100.0% (10/10) | 0.0% (0/10) | 0 (0.0%) |
| `timeout` | 100.0% (10/10) | 0.0% (0/10) | 0 (0.0%) |
| `bad_citation` | 100.0% (10/10) | 0.0% (0/10) | 0 (0.0%) |
| `always_bad` | 100.0% (10/10) | 0.0% (0/10) | 0 (0.0%) |
| `bad_then_good` | 0.0% (0/10) | 100.0% (10/10) | 0 (0.0%) |
| `naive_latest` | 20.0% (2/10) | 0.0% (0/10) | 0 (0.0%) |

## Gates

| Gate | Result |
|---|---|
| unsafe proposal escape is zero | PASS |
| detection recall is 100% | PASS |
| clean false positives are zero | PASS |
| correct citation rate is at least 98% | PASS |
| always_bad cases all end in fallback | PASS |
| bad_citation cases all end in fallback | PASS |
| garbage cases all end in fallback | PASS |
| timeout cases all end in fallback | PASS |
| bad_then_good cases all recover on attempt 2 with the LLM | PASS |

## Reproduce with Ollama

Start Ollama and pull the configured model, then run `LLM_PROVIDER=ollama python eval/run_eval.py --baseline-only --results-json eval/results-ollama.json --results-markdown eval/results-ollama.md` for a separate real-model report. Omit `--baseline-only` to include the deterministic fault-injection suite.

Ollama gates currently use initial engineering targets of zero unsafe escapes, at least 98% policy citations and independent-oracle passes, 100% adversarial safety, at most 20% safe fallback, and p95 latency at or below 120 seconds. The team must review these targets before treating them as release criteria.
