# Company attendance connector setup

Step 3 provides a vendor-neutral adapter plus a synthetic provider. The actual company's webhook signing rules, field mapping, credentials, rate limits, and supported correction types still need confirmation against its API.

## Configure event intake

1. Apply the Supabase workflow migrations and create an organization as described in [Supabase Auth setup](AUTH_SETUP.md).
2. Create an active integration row for that organization:

   ```sql
   insert into public.integrations (organization_id, provider, display_name)
   values ('<organization-uuid>', 'company_api', 'Company attendance')
   returning id;
   ```

3. Put these values in the backend `.env` only:

   ```dotenv
   AUTH_MODE=supabase
   AUTH_ORGANIZATION_ID=<organization-uuid>
   SUPABASE_DATABASE_URL=<server-only-postgres-connection-string>
   ATTENDANCE_INTEGRATION_ID=<integration-uuid>
   INTEGRATION_WEBHOOK_SECRET=<random-secret>
   ATTENDANCE_PROVIDER=mock
   LLM_PROVIDER=mock
   ANALYSIS_MAX_ATTEMPTS=4
   ```

   Generate a local secret with `openssl rand -hex 32`. Keep the database URL, webhook secret, and company API token off the frontend and out of source control.

4. Start FastAPI and the analysis worker, then send the synthetic event set for every built-in detector rule:

   ```bash
   cd backend
   PYTHONPATH=. python scripts/send_synthetic_company_event.py
   ```

   The default command queues 15 signed events using the synthetic provider's fixture records. Keep the built-in rules enabled to create cases for all 15. You can limit the batch with repeated `--scenario RULE-ID` options, such as `--scenario PUNCH-01 --scenario OT-02`. Use `--single` to send one event for a selected source record. The script queues events; the worker processes them asynchronously.

5. Recover missed webhook events from the configured cursor feed:

   ```bash
   PYTHONPATH=. python scripts/sync_company_attendance.py
   ```

The sender posts to `http://127.0.0.1:8000` by default. Pass `--url` to target another development server. Each event ID is unique by default; reusing an ID with the same payload is acknowledged as a duplicate, while a changed payload conflicts. The all-rules batch is available only with `ATTENDANCE_PROVIDER=mock`.

The webhook atomically stores the event envelope and enqueues a `detect` job. It returns without calling the LLM. It does not store punches, schedules, leave details, or a full timecard; if supplied, the company-provided employee display name is retained with the minimal event metadata and case evidence. Cursor sync persists events and their jobs before advancing its checkpoint; re-reading a page is safe. The mock provider includes `tcday_2026-11-03_8F39A` at version `17`. Its source records and simulated corrections live in process memory, so restarting the API resets those upstream fixtures; Supabase still retains the workflow decision and writeback result. Use the company's nonproduction API to demonstrate persistent source-system behavior.

## Run background analysis

Apply every migration in `supabase/migrations` before starting the worker. In particular, `20261104002000_durable_detection_jobs.sql` allows detection jobs to exist before the worker has created an exception case, and `20261104005000_operational_heartbeats.sql` enables the readiness probe to detect a stopped worker. Start the API and worker in separate terminals:

```bash
make backend
make worker
```

For a single queued event, use `cd backend && PYTHONPATH=. python scripts/analysis_worker.py --once`. The worker fetches the changed timecard at the event's source version and a complete bounded two-week history from the company API, runs deterministic attendance rules, retrieves the matching policy passages, asks the configured LLM provider for a bounded proposal, and validates it with the existing simulator. It persists exception cases, proposals, validation checks, source version/hash, and audit events in Supabase. It never writes corrections to the company system; approved writeback is performed only by the HR review flow.

The HTTP provider also requires `GET /v1/attendance/employees/{employee_ref}/timecards?from=YYYY-MM-DD&through=YYYY-MM-DD&include=employee_name,punches,schedule,leave`. The response shape and completeness contract are in [the company integration contract](COMPANY_INTEGRATION_CONTRACT.md). Incomplete history and temporary upstream failures are retried with bounded exponential backoff. After the retry limit, source/API failures leave a failed job for operations review. If deterministic findings exist but the model is unavailable after the retry limit, the worker stores an `ESCALATE` fallback proposal and marks its case `escalated`.

In `AUTH_MODE=supabase`, the HR queue and case review read PostgreSQL workflow cases and re-fetch current attendance evidence from the company provider. Ask ShiftGuard reads organization-scoped case snapshots, stores conversations and cited sources under the signed-in HR user, and keeps them until that user deletes them. Apply `20261104004000_conversation_message_order.sql` after the workflow schema migration. The default demo mode continues to read its separate SQLite fixtures and keeps assistant turns in browser memory.

For actual Ollama generation, set `LLM_PROVIDER=ollama`, configure `OLLAMA_URL` and `OLLAMA_MODEL`, and make sure the API and worker hosts can reach that Ollama server. Ask ShiftGuard is enabled only when the backend health check confirms Ollama and the configured model are reachable. `LLM_PROVIDER=mock` is an offline development provider; it does not call Ollama.

## Configure the HTTP provider

After the company approves the API contract, set:

```dotenv
ATTENDANCE_PROVIDER=http
ATTENDANCE_API_BASE_URL=https://<company-api-host>
ATTENDANCE_API_TOKEN=<server-only-integration-token>
ATTENDANCE_API_TIMEOUT_SECONDS=10
```

The adapter expects `GET /v1/attendance/records/{record_ref}?version=...&include=employee_name,punches,schedule,leave` and `GET /v1/attendance/events?cursor=...&limit=100` to return the normalized shapes in [the contract](COMPANY_INTEGRATION_CONTRACT.md). Approved corrections use `POST /v1/attendance/corrections`, `If-Match`, an idempotency key, and the target source version. The company must implement the contract's cross-record target concurrency check before applying corrections to a record other than the event's trigger record.

## Delivery behavior at this stage

The event route authenticates the HMAC, validates the small allow-listed payload, and persists it in Supabase with `status='queued'` alongside its analysis job. The unique `(integration_id, external_event_id)` key prevents duplicate event rows, and the job has a separate idempotency key. A repeated identical event gets `202` with `duplicate: true`; reusing an ID for changed content gets `409`. The route never calls the LLM.
