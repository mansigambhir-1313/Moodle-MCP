-- Deploy the MCP audit backend to production (activity recording).
-- The 2026-09-07 migration's OAuth table (mcp_oauth_kv) is live, but its mcp_audit
-- half was never applied — so no tool call is being recorded. This migration is a
-- focused, idempotent re-application of ONLY the audit objects + the mcp_audit_writer
-- role + the record RPC + the capture query layer. It does NOT re-touch the live
-- reporting_readonly / mcp_oauth_writer data grants.
-- Apply with the Supabase migration role, never an application credential.

create schema if not exists mcp_audit;
revoke all on schema mcp_audit from public, anon, authenticated;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'mcp_audit_writer') then
    create role mcp_audit_writer nologin;
  end if;
end $$;
grant mcp_audit_writer to authenticator;
grant usage on schema public to mcp_audit_writer;

create table if not exists mcp_audit.users (
  user_id uuid primary key default gen_random_uuid(),
  user_subject text not null unique,
  status text not null default 'active' check (status in ('active','disabled','deleted')),
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);

create table if not exists mcp_audit.user_grants (
  grant_id uuid primary key default gen_random_uuid(),
  user_subject text not null,
  campus text not null,
  role_name text not null,
  grant_version bigint not null default 1,
  effective_at timestamptz not null default now(),
  expires_at timestamptz,
  revoked_at timestamptz,
  actor_subject text
);

create table if not exists mcp_audit.clients (
  client_subject text primary key,
  platform text,
  redirect_origin text,
  approved boolean not null default false,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  revoked_at timestamptz
);

create table if not exists mcp_audit.sessions (
  session_id uuid primary key default gen_random_uuid(),
  session_subject text not null unique,
  user_subject text not null,
  client_subject text,
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  status text not null default 'active' check (status in ('active','ended','revoked'))
);

create table if not exists mcp_audit.tool_calls (
  event_id uuid not null,
  occurred_at timestamptz not null default now(),
  user_subject text not null,
  session_subject text,
  client_subject text,
  tool_name text not null,
  campus_scope text,
  outcome text not null check (outcome in ('attempt','success','failure')),
  error_code text,
  duration_ms integer not null check (duration_ms >= 0),
  request_id text not null,
  metadata jsonb not null default '{}'::jsonb,
  primary key (event_id, occurred_at)
) partition by range (occurred_at);

create table if not exists mcp_audit.tool_calls_2026_q3
  partition of mcp_audit.tool_calls for values from ('2026-07-01') to ('2026-10-01');
create table if not exists mcp_audit.tool_calls_2026_q4
  partition of mcp_audit.tool_calls for values from ('2026-10-01') to ('2027-01-01');
create table if not exists mcp_audit.tool_calls_2027_h1
  partition of mcp_audit.tool_calls for values from ('2027-01-01') to ('2027-07-01');
create table if not exists mcp_audit.tool_calls_default
  partition of mcp_audit.tool_calls default;

alter table mcp_audit.tool_calls
  drop constraint if exists tool_calls_outcome_check;
alter table mcp_audit.tool_calls
  add constraint tool_calls_outcome_check
  check (outcome in ('attempt','success','failure'));

create index if not exists mcp_tool_calls_time_brin
  on mcp_audit.tool_calls using brin (occurred_at);
create index if not exists mcp_tool_calls_user_time_idx
  on mcp_audit.tool_calls (user_subject, occurred_at desc);
create index if not exists mcp_tool_calls_tool_time_idx
  on mcp_audit.tool_calls (tool_name, occurred_at desc);
-- Activity-capture query ergonomics (from 2026-09-14): fast lookups into the widened
-- metadata blob (identity.email, arguments, result, source_ip).
create index if not exists mcp_tool_calls_metadata_gin
  on mcp_audit.tool_calls using gin (metadata jsonb_path_ops);

create table if not exists mcp_audit.security_events (
  event_id uuid primary key default gen_random_uuid(),
  occurred_at timestamptz not null default now(),
  event_type text not null,
  actor_subject text,
  target_subject text,
  outcome text not null,
  request_id text,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists mcp_audit.consents (
  consent_id uuid primary key default gen_random_uuid(),
  user_subject text not null,
  purpose text not null,
  policy_version text not null,
  accepted_at timestamptz not null default now(),
  withdrawn_at timestamptz,
  unique (user_subject, purpose, policy_version)
);

alter table mcp_audit.users enable row level security;
alter table mcp_audit.user_grants enable row level security;
alter table mcp_audit.clients enable row level security;
alter table mcp_audit.sessions enable row level security;
alter table mcp_audit.tool_calls enable row level security;
alter table mcp_audit.security_events enable row level security;
alter table mcp_audit.consents enable row level security;

-- Lock down every mcp_audit table. public/anon/authenticated always exist; the
-- other roles are optional, so revoke from them ONLY if present — a missing role
-- (e.g. mcp_oauth_writer) must not abort the migration before the RPC below is created.
revoke all on all tables in schema mcp_audit from public, anon, authenticated;
do $$
declare r text;
begin
  foreach r in array array['reporting_readonly','mcp_oauth_writer','mcp_audit_writer'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on all tables in schema mcp_audit from %I', r);
    end if;
  end loop;
end $$;

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

create or replace function public.purge_expired_mcp_security_data()
returns table(oauth_rows bigint, audit_rows bigint)
language plpgsql
security definer
set search_path = ''
as $$
declare
  oauth_count bigint;
  audit_count bigint;
begin
  delete from public.mcp_oauth_kv where expires_at is not null and expires_at < now();
  get diagnostics oauth_count = row_count;
  delete from mcp_audit.tool_calls where occurred_at < now() - interval '180 days';
  get diagnostics audit_count = row_count;
  return query select oauth_count, audit_count;
end;
$$;
revoke all on function public.purge_expired_mcp_security_data()
  from public, anon, authenticated, reporting_readonly, mcp_oauth_writer, mcp_audit_writer;
grant execute on function public.purge_expired_mcp_security_data() to service_role;

-- Analyst-facing flattened view over the captured metadata (2026-09-14).
create or replace view mcp_audit.v_activity as
select
  event_id, occurred_at, tool_name, outcome, error_code, duration_ms, request_id,
  user_subject, session_subject, client_subject, campus_scope,
  metadata #>> '{identity,email}'    as user_email,
  metadata #>> '{identity,name}'     as user_name,
  metadata #>  '{identity,campuses}' as user_campuses,
  metadata ->> 'source_ip'           as source_ip,
  metadata ->  'arguments'           as arguments,
  metadata ->  'result'              as result,
  metadata ->> 'server_version'      as server_version
from mcp_audit.tool_calls;
