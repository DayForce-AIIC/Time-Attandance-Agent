-- ShiftGuard workflow state only. Company employee and attendance records stay
-- in the company's HR/timekeeping system and are referenced by opaque IDs.

create extension if not exists pgcrypto with schema extensions;

create table public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now()
);

create table public.user_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  organization_id uuid not null references public.organizations(id) on delete restrict,
  role text not null check (role in ('HR', 'manager', 'integration_service')),
  manager_ref text,
  external_user_ref text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint user_profiles_manager_ref_required check (role <> 'manager' or manager_ref is not null)
);
create index user_profiles_organization_idx on public.user_profiles(organization_id);

create table public.integrations (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  provider text not null,
  display_name text not null,
  status text not null default 'active' check (status in ('active', 'paused', 'error')),
  credential_ref text,
  settings jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_id, provider, display_name)
);

create table public.sync_cursors (
  id uuid primary key default gen_random_uuid(),
  integration_id uuid not null references public.integrations(id) on delete cascade,
  cursor_name text not null,
  cursor_value text,
  updated_at timestamptz not null default now(),
  unique (integration_id, cursor_name)
);

create table public.inbound_events (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  integration_id uuid not null references public.integrations(id) on delete cascade,
  external_event_id text not null,
  event_type text not null,
  external_employee_ref text,
  source_record_refs jsonb not null default '[]'::jsonb,
  source_version text,
  payload_hash text not null,
  minimal_payload jsonb not null default '{}'::jsonb,
  status text not null default 'received'
    check (status in ('received', 'queued', 'processing', 'processed', 'ignored', 'failed')),
  received_at timestamptz not null default now(),
  processed_at timestamptz,
  error_code text,
  unique (integration_id, external_event_id)
);
create index inbound_events_status_received_idx on public.inbound_events(status, received_at);

create table public.exception_cases (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  integration_id uuid references public.integrations(id) on delete set null,
  case_key text not null,
  external_employee_ref text not null,
  source_record_refs jsonb not null default '[]'::jsonb,
  source_version text,
  source_updated_at timestamptz,
  source_hash text,
  minimal_evidence_snapshot jsonb,
  rule_id text not null,
  severity text not null check (severity in ('low', 'medium', 'high', 'critical')),
  status text not null default 'open'
    check (status in ('open', 'pending_review', 'escalated', 'resolved', 'rejected', 'superseded')),
  detected_at timestamptz not null default now(),
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_id, case_key)
);
create index exception_cases_org_status_idx
  on public.exception_cases(organization_id, status, detected_at desc);
create index exception_cases_source_refs_gin_idx
  on public.exception_cases using gin(source_record_refs);

create table public.analysis_jobs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  exception_case_id uuid not null references public.exception_cases(id) on delete cascade,
  inbound_event_id uuid references public.inbound_events(id) on delete set null,
  job_type text not null default 'analyze',
  idempotency_key text not null,
  status text not null default 'queued'
    check (status in ('queued', 'processing', 'completed', 'retry', 'failed', 'cancelled')),
  attempts integer not null default 0 check (attempts >= 0),
  available_at timestamptz not null default now(),
  locked_at timestamptz,
  completed_at timestamptz,
  last_error_code text,
  created_at timestamptz not null default now(),
  unique (organization_id, idempotency_key)
);
create index analysis_jobs_claim_idx on public.analysis_jobs(status, available_at, created_at)
  where status in ('queued', 'retry');

create table public.proposals (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  exception_case_id uuid not null references public.exception_cases(id) on delete cascade,
  analysis_job_id uuid references public.analysis_jobs(id) on delete set null,
  version integer not null check (version > 0),
  source text not null check (source in ('llm', 'fallback', 'rule')),
  model text not null,
  prompt_version text not null,
  expected_source_version text,
  expected_source_hash text,
  proposal jsonb not null,
  policy_ids text[] not null default '{}',
  attempts integer not null default 1 check (attempts > 0),
  latency_ms integer not null default 0 check (latency_ms >= 0),
  created_at timestamptz not null default now(),
  unique (exception_case_id, version)
);
create index proposals_case_created_idx on public.proposals(exception_case_id, created_at desc);

create table public.validation_results (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  proposal_id uuid not null references public.proposals(id) on delete cascade,
  passed boolean not null,
  checks jsonb not null default '[]'::jsonb,
  validator_version text not null,
  created_at timestamptz not null default now()
);
create index validation_results_proposal_idx on public.validation_results(proposal_id, created_at desc);

create table public.decisions (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  exception_case_id uuid not null references public.exception_cases(id) on delete restrict,
  proposal_id uuid references public.proposals(id) on delete set null,
  decided_by uuid not null references auth.users(id) on delete restrict,
  action text not null check (action in ('approve', 'edit', 'reject')),
  note text,
  final_correction jsonb,
  expected_source_version text,
  writeback_status text not null default 'not_requested'
    check (writeback_status in ('not_requested', 'pending', 'applied', 'stale', 'failed')),
  external_operation_id text,
  writeback_result jsonb,
  decided_at timestamptz not null default now(),
  writeback_completed_at timestamptz
);
create index decisions_case_decided_idx on public.decisions(exception_case_id, decided_at desc);

create table public.audit_events (
  id bigint generated always as identity primary key,
  organization_id uuid not null references public.organizations(id) on delete cascade,
  trace_id text not null,
  exception_case_id uuid references public.exception_cases(id) on delete set null,
  sequence_number bigint not null check (sequence_number > 0),
  event_type text not null,
  payload jsonb not null default '{}'::jsonb,
  previous_hash text not null,
  event_hash text not null,
  created_at timestamptz not null default now(),
  unique (organization_id, sequence_number)
);
create index audit_events_case_idx on public.audit_events(organization_id, exception_case_id, id);

create table public.conversations (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  title text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index conversations_user_updated_idx on public.conversations(organization_id, user_id, updated_at desc);

create table public.messages (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  role text not null check (role in ('user', 'assistant', 'system')),
  content text not null,
  model text,
  source_references jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);
create index messages_conversation_created_idx on public.messages(conversation_id, created_at);

-- Audit history must be append-only, including for service-role application code.
create function public.reject_audit_event_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'audit_events is append-only';
end;
$$;
create trigger audit_events_no_update
  before update or delete on public.audit_events
  for each row execute function public.reject_audit_event_mutation();

-- Do not expose workflow records directly to browser roles. Step 2 will add
-- organization-scoped policies only if direct authenticated access is needed.
alter table public.organizations enable row level security;
alter table public.user_profiles enable row level security;
alter table public.integrations enable row level security;
alter table public.sync_cursors enable row level security;
alter table public.inbound_events enable row level security;
alter table public.exception_cases enable row level security;
alter table public.analysis_jobs enable row level security;
alter table public.proposals enable row level security;
alter table public.validation_results enable row level security;
alter table public.decisions enable row level security;
alter table public.audit_events enable row level security;
alter table public.conversations enable row level security;
alter table public.messages enable row level security;

revoke all on public.organizations, public.user_profiles, public.integrations,
  public.sync_cursors, public.inbound_events, public.exception_cases,
  public.analysis_jobs, public.proposals, public.validation_results,
  public.decisions, public.audit_events, public.conversations, public.messages
  from anon, authenticated;
grant all on public.organizations, public.user_profiles, public.integrations,
  public.sync_cursors, public.inbound_events, public.exception_cases,
  public.analysis_jobs, public.proposals, public.validation_results,
  public.decisions, public.audit_events, public.conversations, public.messages
  to service_role;
grant usage, select on all sequences in schema public to service_role;
