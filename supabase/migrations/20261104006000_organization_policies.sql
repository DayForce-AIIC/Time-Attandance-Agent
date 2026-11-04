-- Organization-scoped attendance rules and company reference policies.
-- Standard rule evaluation stays in the deterministic engine; custom policies
-- are supplied to the LLM as evidence and do not execute new detector logic.

create table public.organization_policies (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  policy_id text not null check (length(policy_id) between 1 and 64),
  kind text not null check (kind in ('standard', 'custom')),
  rule_id text,
  category text not null,
  title text not null check (length(title) between 3 and 160),
  text text not null check (length(text) between 20 and 12000),
  severity text check (severity is null or severity in ('low', 'medium', 'high', 'critical')),
  applies_to_rule_ids text[] not null default '{}',
  is_active boolean not null default true,
  revision integer not null default 1 check (revision > 0),
  created_by uuid references auth.users(id) on delete set null,
  updated_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  unique (organization_id, policy_id),
  check (
    (kind = 'standard' and rule_id is not null and severity is not null)
    or (kind = 'custom' and rule_id is null and severity is null)
  )
);
create index organization_policies_active_idx
  on public.organization_policies(organization_id, is_active, kind);

create table public.organization_policy_revisions (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  policy_id uuid not null references public.organization_policies(id) on delete restrict,
  revision integer not null check (revision > 0),
  change_kind text not null check (change_kind in ('created', 'updated', 'archived')),
  snapshot jsonb not null,
  changed_by uuid references auth.users(id) on delete set null,
  changed_at timestamptz not null default now(),
  unique (policy_id, revision)
);
create index organization_policy_revisions_org_idx
  on public.organization_policy_revisions(organization_id, changed_at desc);

create function public.reject_organization_policy_revision_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'organization_policy_revisions is append-only';
end;
$$;
create trigger organization_policy_revisions_no_update
  before update or delete on public.organization_policy_revisions
  for each row execute function public.reject_organization_policy_revision_mutation();

alter table public.proposals
  add column if not exists policy_snapshot jsonb not null default '[]'::jsonb;

alter table public.organization_policies enable row level security;
alter table public.organization_policy_revisions enable row level security;
revoke all on public.organization_policies, public.organization_policy_revisions
  from anon, authenticated;
grant all on public.organization_policies, public.organization_policy_revisions
  to service_role;
