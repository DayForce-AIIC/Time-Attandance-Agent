create table public.service_heartbeats (
  organization_id uuid not null references public.organizations(id) on delete cascade,
  service_name text not null check (service_name in ('analysis_worker')),
  instance_id text not null check (length(instance_id) between 1 and 200),
  started_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  primary key (organization_id, service_name, instance_id)
);

create index service_heartbeats_recent_idx
  on public.service_heartbeats(organization_id, service_name, last_seen_at desc);

alter table public.service_heartbeats enable row level security;
revoke all on public.service_heartbeats from anon, authenticated;
grant all on public.service_heartbeats to service_role;
