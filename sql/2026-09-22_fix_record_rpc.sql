-- FIX: the audit RPC public.record_mcp_tool_call was never created in prod.
-- Root cause: the 2026-09-18 migration aborted at its `revoke ... from ... mcp_oauth_writer`
-- (a role that does not exist), BEFORE reaching the CREATE FUNCTION below. The mcp_audit
-- tables/partitions exist, but with no RPC every audit write 404s silently, so 0 rows are
-- recorded regardless of SUPABASE_AUDIT_KEY. This re-applies ONLY the role + RPC + grants,
-- idempotently, skipping the abort-prone revoke.
-- Apply with the Supabase migration role (SQL editor / CLI), never an application credential.
-- Project: sadbfvfcmmxgtatfjfmc

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'mcp_audit_writer') then
    create role mcp_audit_writer nologin;
  end if;
end $$;
grant mcp_audit_writer to authenticator;
grant usage on schema public to mcp_audit_writer;

create or replace function public.record_mcp_tool_call(
  p_event_id uuid,
  p_user_subject text,
  p_tool_name text,
  p_campus_scope text,
  p_outcome text,
  p_error_code text,
  p_duration_ms integer,
  p_request_id text,
  p_session_subject text,
  p_client_subject text,
  p_metadata jsonb
) returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_outcome not in ('attempt','success','failure') or p_duration_ms < 0 then
    raise exception 'invalid audit event';
  end if;
  insert into mcp_audit.users (user_subject)
  values (left(p_user_subject, 128))
  on conflict (user_subject) do update set last_seen_at = now();
  if p_client_subject is not null then
    insert into mcp_audit.clients (client_subject, last_seen_at)
    values (left(p_client_subject, 128), now())
    on conflict (client_subject) do update set last_seen_at = now();
  end if;
  if p_session_subject is not null then
    insert into mcp_audit.sessions (
      session_subject, user_subject, client_subject
    ) values (
      left(p_session_subject, 128), left(p_user_subject, 128),
      left(p_client_subject, 128)
    ) on conflict (session_subject) do nothing;
  end if;
  insert into mcp_audit.tool_calls (
    event_id, user_subject, tool_name, campus_scope, outcome, error_code,
    duration_ms, request_id, session_subject, client_subject, metadata
  ) values (
    p_event_id, left(p_user_subject, 128), left(p_tool_name, 128),
    left(p_campus_scope, 64), p_outcome, left(p_error_code, 64),
    p_duration_ms, left(p_request_id, 128), left(p_session_subject, 128),
    left(p_client_subject, 128), coalesce(p_metadata, '{}'::jsonb)
  );
end;
$$;

revoke all on function public.record_mcp_tool_call(
  uuid,text,text,text,text,text,integer,text,text,text,jsonb) from public, anon, authenticated;
grant execute on function public.record_mcp_tool_call(
  uuid,text,text,text,text,text,integer,text,text,text,jsonb) to mcp_audit_writer;

-- Sanity: prove the function now exists (should return one row).
select 'record_mcp_tool_call created' as status,
       pg_get_function_identity_arguments(p.oid) as args
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname = 'record_mcp_tool_call';
