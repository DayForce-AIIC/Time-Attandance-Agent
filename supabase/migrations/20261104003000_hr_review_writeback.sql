-- HR-approved corrections can require follow-up when the company API is slow
-- or temporarily unavailable. These states keep the case visible in the queue.

alter table public.exception_cases
  drop constraint if exists exception_cases_status_check;

alter table public.exception_cases
  add constraint exception_cases_status_check
  check (
    status in (
      'open',
      'pending_review',
      'escalated',
      'resolved',
      'rejected',
      'superseded',
      'writeback_pending',
      'writeback_failed'
    )
  );

create index exception_cases_writeback_queue_idx
  on public.exception_cases(organization_id, status, updated_at desc)
  where status in ('writeback_pending', 'writeback_failed');
