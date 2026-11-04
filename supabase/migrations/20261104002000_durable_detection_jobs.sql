-- Detection jobs are created from inbound events before an exception exists.
-- An event may produce zero, one, or several cases, so this link is optional.
alter table public.analysis_jobs
  alter column exception_case_id drop not null;

create index if not exists analysis_jobs_inbound_event_idx
  on public.analysis_jobs(organization_id, inbound_event_id)
  where inbound_event_id is not null;
