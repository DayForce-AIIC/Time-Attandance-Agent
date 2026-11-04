# Operations and deployment

Step 7 adds operational signals and deployment artifacts. It does not deploy the application to a cloud account or provision a company integration; choose those environments and supply credentials before connecting real systems.

## Runtime services

The authenticated workflow needs three reachable services:

- **Supabase PostgreSQL and Auth** for workflow data and HR sign-in.
- **FastAPI** for the web API and company event receiver.
- **The analysis worker** as a continuously running process using the same image and server-side configuration as FastAPI.

Ollama must be reachable from both FastAPI and the worker when `LLM_PROVIDER=ollama`. The browser frontend can be hosted separately, with `/api/v1` and `/integrations/v1` routed to FastAPI. Keep the frontend, API, worker, Supabase, company API, and Ollama on private or approved networks where possible.

The backend image can be built from the repository root:

```bash
docker build -f backend/Dockerfile -t shiftguard-backend:local .
```

Run the API and worker as separate services. Inject settings through the host's secret store in deployed environments; for local development only, `--env-file .env` can be used:

```bash
docker run --env-file .env -e DB_PATH=/var/lib/shiftguard/shiftguard.db -p 8000:8000 shiftguard-backend:local
docker run --env-file .env -e DB_PATH=/var/lib/shiftguard/shiftguard.db --entrypoint python shiftguard-backend:local scripts/analysis_worker.py
```

Use a process supervisor to restart either service on exit. Run `scripts/sync_company_attendance.py` on a schedule only after the company cursor feed is configured; webhook delivery should be recovered by this job after outages.

## Health and worker monitoring

- `GET /api/v1/health` reports API liveness and configured model availability.
- `GET /api/v1/health/ready` reports database, attendance provider configuration, model, and worker status. It returns `503` until all required checks pass in the selected environment.
- In Supabase mode, the worker writes a heartbeat every `ANALYSIS_WORKER_HEARTBEAT_SECONDS`. A heartbeat older than `ANALYSIS_WORKER_HEARTBEAT_TIMEOUT_SECONDS` makes readiness fail. Apply `20261104005000_operational_heartbeats.sql` before using this readiness check.

Collect JSON logs from both API and worker. Join a request with background work using `trace_id`, `integration_event_id`, `analysis_job_id`, `exception_case_id`, `decision_id`, and `external_operation_id`. Logs contain identifiers and safe error codes; they must not contain request bodies, attendance evidence, access tokens, database URLs, or webhook secrets.

## Rate limits and alerts

The API has process-local sliding-window limits for Ask ShiftGuard, inbound events, and approval/writeback routes. Defaults are in `.env.example`; `Retry-After` is returned when a request is limited. These protect one process and are defense in depth. Configure shared rate limits at the deployment gateway too, especially when running multiple API replicas. Ensure the gateway forwards the real client address through a trusted configuration before relying on IP-based event limits.

Repeated worker, Ollama, attendance provider, database intake, and writeback failures emit structured alerts after `OPS_ALERT_FAILURE_THRESHOLD` occurrences in `OPS_ALERT_WINDOW_SECONDS`. If `OPS_ALERT_WEBHOOK_URL` is configured, a JSON POST is sent with the component, safe error code, count, and relevant trace/workflow IDs. `OPS_ALERT_COOLDOWN_SECONDS` prevents repeated notifications. Without a webhook, the alert remains in application logs for the log platform to route.

The webhook URL is a secret and must be configured only in the API/worker secret store. The alert body intentionally excludes employee data, questions, model prompts, and response content. Keep the URL on an approved HTTPS endpoint outside local development.

## Secrets and environment gates

Keep `.env` and deployed credentials out of Git and container images. The root `.dockerignore` excludes local env files. Put `SUPABASE_DATABASE_URL`, `ATTENDANCE_API_TOKEN`, `INTEGRATION_WEBHOOK_SECRET`, and `OPS_ALERT_WEBHOOK_URL` in the deployment platform's secret store. Browser configuration contains only the Supabase URL and publishable key.

`ENVIRONMENT=production` prevents startup unless Supabase Auth is enabled, the Supabase URL and database connection use HTTPS/TLS, CORS origins are explicit HTTPS origins, the company provider is `http`, and the configured LLM provider is Ollama. Use `ENVIRONMENT=development` or `staging` with the synthetic provider for rehearsals. Do not use production employee data with a Supabase Free project.

## Backups and restore drills

1. Agree on recovery point and recovery time objectives with the organization operating ShiftGuard.
2. Export or configure database backups using the [current Supabase backup guidance](https://supabase.com/docs/guides/platform/backups). Store exports encrypted and access controlled, separate from application credentials.
3. Record the backup timestamp, project, schema migration version, and retention expiry without recording credentials in the log.
4. Restore into an isolated staging project on a recurring schedule and before high-risk schema changes.
5. Verify sign-in profiles, one synthetic event/job, a case and decision, audit-chain verification, and an Ask ShiftGuard conversation. Confirm the restored app uses the intended integration and cannot contact a production attendance API.
6. Record the drill result and remove the temporary restored data according to the agreed retention policy.

Supabase Free projects can pause after inactivity. Keep the development project synthetic; select a company-approved managed plan before a production rollout.

## Failure triage

1. Use the response `X-Request-ID` or the workflow ID in the UI/API result to find matching structured log entries.
2. For `analysis_worker` readiness failures, check worker process health and the latest row in `public.service_heartbeats` for the configured organization.
3. For retrying/failed jobs, inspect `public.analysis_jobs` using its `status`, `attempts`, `last_error_code`, `available_at`, and `inbound_event_id`; match the event in `public.inbound_events` by ID.
4. For a failed writeback, use the decision ID and `external_operation_id` to inspect the company response and retry through the HR UI only after confirming the company's idempotency behavior.
5. For integrity questions, use the application's audit verification endpoint and compare the affected case's audit IDs and trace IDs.

Never retry a company correction with a new idempotency key. The HR retry route deliberately uses the original decision key.
