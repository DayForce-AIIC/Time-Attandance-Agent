# Supabase Auth setup

Step 2 adds email/password sign-in for provisioned HR and manager accounts. Public sign-up is not exposed. FastAPI verifies each access-token signature, issuer, audience, and expiration, then reads `organization_id`, `role`, and `manager_ref` from the server-managed `public.user_profiles` row. The browser's manager selector is used only in `AUTH_MODE=demo`; Supabase mode ignores `X-Manager-Id`.

## 1. Apply the migrations

Link the repository to the Supabase project and apply both migrations as described in the [Supabase project setup](../README.md#supabase-workflow-database-foundation). The profile mapping column is added in `20261104001000_manager_profile_scope.sql` so it also applies when the original Step 1 migration was already deployed.

## 2. Create an organization and provision users

Create HR accounts in Supabase Dashboard → Authentication → Users, using the invite flow. Do not enable public self-registration for this prototype. Create one organization in the SQL Editor:

```sql
insert into public.organizations (name)
values ('Example Company')
returning id;
```

Copy that UUID and the Auth user's UUID, then create a profile using the SQL Editor:

```sql
-- HR can review all teams in this single-organization API deployment.
insert into public.user_profiles (user_id, organization_id, role)
values ('<auth-user-uuid>', '<organization-uuid>', 'HR');

-- For a manager, use a manager_ref that maps to their assigned team.
insert into public.user_profiles (user_id, organization_id, role, manager_ref)
values ('<manager-auth-user-uuid>', '<organization-uuid>', 'manager', 'm-001');
```

`manager_ref` is server-managed and cannot be selected in the UI. It must map to an existing team in the current synthetic SQLite adapter. The company connector will replace this demo mapping with the company's manager reference in a later step. Keep `integration_service` identities out of the interactive HR interface.

The migration enables RLS and grants no table access to `anon` or `authenticated`; profile and workflow reads go through the backend's private PostgreSQL connection. Do not put `SUPABASE_DATABASE_URL`, a database password, or a service-role key in frontend variables.

## 3. Configure backend and frontend

Set the backend `.env` values:

```dotenv
AUTH_MODE=supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_DATABASE_URL=postgresql://... # server-only connection string, with sslmode=require
AUTH_ORGANIZATION_ID=<organization-uuid>
```

`AUTH_ORGANIZATION_ID` binds this API process to one organization while the application's attendance adapter is still SQLite. Requests from users outside that organization are rejected. Set `SUPABASE_JWT_SECRET` only if the Supabase project still signs tokens with its legacy HS256 secret; projects using asymmetric signing keys are verified against the project's JWKS endpoint.

Create `frontend/.env.local`:

```dotenv
VITE_AUTH_MODE=supabase
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
```

The publishable key is intended for browser use; it does not replace backend authorization. Restart both servers after changing environment variables. Sign-in uses existing accounts only; the `/api/v1/me` endpoint returns the backend-resolved role and organization.

## Current boundary

Supabase mode currently protects the synthetic local review data in a single configured organization. The company API connector and PostgreSQL-backed case workflow are still future steps, so do not connect real employee data to this SQLite-backed prototype. The organization pin and team-role checks provide safe demo isolation; a shared multi-organization deployment needs the API's case repository to use the tenant-scoped PostgreSQL workflow tables before it is enabled.
