# ShiftGuard

[![CI](https://github.com/DayForce-AIIC/Time-Attandance-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/DayForce-AIIC/Time-Attandance-Agent/actions/workflows/ci.yml)

ShiftGuard helps managers review timecard exceptions using policy-grounded suggestions, deterministic safety checks, and an explicit human decision before any correction is applied.

## The problem and workflow

Managers must compare punches, schedules, and policy to resolve missed punches and other timecard exceptions. ShiftGuard brings that evidence into one review flow:

1. Deterministic rules detect exceptions and calculate safe correction constraints.
2. The proposal service gives the model the exception evidence and relevant policy passages, then validates its response in code.
3. Failed proposals are retried once; if they still fail validation, ShiftGuard escalates the case without an AI suggestion.
4. A manager reviews the case and approves, edits, or rejects the suggestion. Edits are validated again. Only an approved decision applies a correction.
5. Each step is recorded in a hash-chained audit log. Managers can review the case history and check chain integrity.

The prototype uses synthetic timecard and policy data. It is designed to demonstrate safe AI-assisted review, not to calculate payroll.

## Features

- **Manager queue:** filter exceptions by status, severity, and rule; switch between seeded demo managers.
- **Case review:** inspect the timecard, schedule, correction window, weekly hours, and retrieved policy passages alongside the proposal.
- **Safety harness:** review the V-01–V-10 validation checklist, including schema, allowed action, time window, citations, simulated side effects, and prompt-injection checks.
- **Human decisions:** approve, edit, or reject proposals. Manager edits pass through the same deterministic validation.
- **Audit and metrics:** inspect a case's event history, verify the audit hash chain, and review workflow and safety metrics.
- **Fault simulation:** exercise provider failure modes in the local demo and observe retry or escalation behavior.
- **Offline evaluation:** run 48 labelled cases through the mock provider and an independent oracle.

## Architecture and key decisions

```text
React + TypeScript UI
        │ REST / OpenAPI
        ▼
FastAPI ── deterministic detection ── SQLite
        │
        ├── policy retrieval ── proposal client (mock by default, Ollama optional)
        │                           │
        └── deterministic validation and simulation
                                    │
                         manager decision endpoint
                                    │
                    correction + hash-chained audit log
```

Detection, constraints, proposal validation, simulation, and applying approved changes are deterministic backend code. The model can suggest a bounded correction, but it cannot write punches or schedules. The frontend validates API responses with Zod and uses the OpenAPI contract to generate TypeScript API types.

**Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLite, React 19, TypeScript 5, Vite 6, TanStack Query, Zod 4, Vitest, and React Testing Library. The mock LLM provider is the default; Ollama is optional.

## Run locally

Requirements: Python 3.11 or newer and Node.js 20 or newer.

From the repository root, create the Python environment and install backend dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cp .env.example .env
```

Start the API in one terminal:

```bash
cd backend
PYTHONPATH=. uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

On first start, the API initializes and seeds the SQLite database when `AUTO_SEED=true` and the database does not already exist. The default database path is `./shiftguard.db` relative to the backend working directory. The mock provider and seeded data work offline.

Start the frontend in a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). Vite proxies `/api` requests to the backend at `http://localhost:8000`. The interactive API documentation is at [http://localhost:8000/docs](http://localhost:8000/docs).

The **Reset demo data** control recreates the seeded demo workflow. It deletes the current local demo records, so use it only with a disposable demo database.

### Optional Ollama provider

Start Ollama and pull the configured model, then start the backend with `LLM_PROVIDER=ollama`:

```bash
ollama pull qwen2.5:7b-instruct
cd backend
LLM_PROVIDER=ollama PYTHONPATH=. uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Configure the provider URL and model with `OLLAMA_URL` and `OLLAMA_MODEL`. Available settings and defaults are listed in [.env.example](.env.example). `DEMO_NOW` sets the deterministic local demo clock.

## Development checks

Run backend tests from the repository root with the virtual environment active:

```bash
make test
```

Run all backend, evaluation, and frontend CI checks locally with:

```bash
make all
```

Run frontend checks:

```bash
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
```

Regenerate the OpenAPI document and frontend API types with:

```bash
make openapi
```

The OpenAPI document is stored at [docs/openapi.json](docs/openapi.json), and generated frontend types are in `frontend/src/api/openapi-types.ts`.

## Evaluation

Reproduce the offline evaluation from the repository root:

```bash
python eval/run_eval.py
```

The current mock-provider results are recorded in [eval/results.md](eval/results.md): 100% detection recall (46/46 expected findings), zero clean false positives, 100% first-pass validation, and zero unsafe proposal escapes. Fault-injection gates also verify retry and escalation behavior. Evaluation numbers describe this synthetic dataset; they are not production performance claims.

To evaluate with Ollama, start the service, pull the configured model, and run:

```bash
LLM_PROVIDER=ollama python eval/run_eval.py
```

## Repository layout

```text
backend/        FastAPI application, deterministic rules, proposal pipeline, and tests
frontend/       React manager workflow and frontend tests
docs/           OpenAPI contract
eval/           Labelled cases, independent oracle, evaluation runner, and results
```

## Scope and limitations

This is a local prototype with synthetic data. Manager selection and team scoping use a demo `X-Manager-Id` header; there is no real authentication or identity-backed access control, and no multi-tenant isolation. Time values are naive site-local timestamps; daylight-saving-time handling, payroll calculation, and multiple shifts per day are outside scope. Do not expose the demo server to the public internet or use it for real employee records.
