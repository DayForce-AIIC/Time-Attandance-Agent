-- Add this separately so a project that already applied the initial workflow
-- schema also receives the server-side manager-to-team mapping.
alter table public.user_profiles
  add column if not exists manager_ref text;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'user_profiles_manager_ref_required'
      and conrelid = 'public.user_profiles'::regclass
  ) then
    alter table public.user_profiles
      add constraint user_profiles_manager_ref_required
      check (role <> 'manager' or manager_ref is not null);
  end if;
end;
$$;
