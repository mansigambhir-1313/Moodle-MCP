-- Security-advisor fixes for the Moodle Data project (sadbfvfcmmxgtatfjfmc).
-- Found via Supabase security advisor + grant/RLS audit (2026-09-25).
-- Apply in the Supabase SQL editor with the migration role (NOT an app credential).
-- Both are safe: service_role (agent) bypasses RLS and keeps EXECUTE, so legitimate
-- access is unaffected; these only close anon/authenticated exposure.

-- 1) ERROR: public.report_accuracy has RLS OFF → anon/authenticated can read 389
--    student-linked rows (student_id, campus, batch, trimester, scores, detail) directly
--    via PostgREST, bypassing the MCP + campus scoping. A reporting_readonly_select policy
--    already exists but is inert while RLS is off. Enabling RLS activates that policy and
--    default-denies anon/authenticated.
alter table public.report_accuracy enable row level security;

-- 2) WARN: public.purge_expired_mcp_security_data() is a SECURITY DEFINER function that
--    anon + authenticated can call via /rest/v1/rpc/ — i.e. anyone with the (semi-public)
--    anon key could trigger the retention purge. Lock it to service_role (which the pg_cron
--    job uses) only.
revoke execute on function public.purge_expired_mcp_security_data() from anon, authenticated, public;

-- Sanity checks (should return the safe state):
--   RLS now on report_accuracy (expect true):
select relrowsecurity as report_accuracy_rls_on
from pg_class where oid = 'public.report_accuracy'::regclass;
--   anon/authenticated can no longer execute the purge (expect false, false):
select has_function_privilege('anon','public.purge_expired_mcp_security_data()','execute') as anon_can_purge,
       has_function_privilege('authenticated','public.purge_expired_mcp_security_data()','execute') as auth_can_purge;

-- Optional, low priority (advisor WARN): move pg_net out of the public schema.
-- create schema if not exists extensions;
-- alter extension pg_net set schema extensions;
