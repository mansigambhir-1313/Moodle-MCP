-- Moodle MCP runtime security and privacy-safe audit foundation.
-- Apply with the Supabase migration role, never an application credential.

create schema if not exists mcp_audit;
revoke all on schema mcp_audit from public, anon, authenticated;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'reporting_readonly') then
    create role reporting_readonly nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'mcp_oauth_writer') then
    create role mcp_oauth_writer nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'mcp_audit_writer') then
    create role mcp_audit_writer nologin;
  end if;
end $$;
grant reporting_readonly, mcp_oauth_writer, mcp_audit_writer to authenticator;
grant usage on schema public to reporting_readonly, mcp_oauth_writer, mcp_audit_writer;

create table if not exists public.mcp_faculty (
  email text primary key check (email = lower(email)),
  name text,
  campuses jsonb not null,
  active boolean not null default true,
  granted_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  revoked_at timestamptz
);
alter table public.mcp_faculty enable row level security;
revoke all on public.mcp_faculty from public, anon, authenticated;
grant select on public.mcp_faculty to reporting_readonly;
drop policy if exists reporting_readonly_select on public.mcp_faculty;
create policy reporting_readonly_select on public.mcp_faculty
  for select to reporting_readonly using (true);

-- OAuth state is deliberately isolated from both student data and audit data.
create table if not exists public.mcp_oauth_kv (
  collection text not null,
  key text not null,
  value text not null,
  expires_at timestamptz,
  updated_at timestamptz not null default now(),
  primary key (collection, key)
);
create index if not exists mcp_oauth_kv_expires_idx
  on public.mcp_oauth_kv (expires_at) where expires_at is not null;
alter table public.mcp_oauth_kv enable row level security;
revoke all on public.mcp_oauth_kv from public, anon, authenticated, reporting_readonly;
grant select, insert, update, delete on public.mcp_oauth_kv to mcp_oauth_writer;
drop policy if exists mcp_oauth_writer_crud on public.mcp_oauth_kv;
create policy mcp_oauth_writer_crud on public.mcp_oauth_kv
  for all to mcp_oauth_writer using (true) with check (true);

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

revoke all on all tables in schema mcp_audit from public, anon, authenticated,
  reporting_readonly, mcp_oauth_writer, mcp_audit_writer;

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

-- The MCP reads cached one-page narratives but never their rendered HTML. The
-- agent migration owns this table, so keep a fresh-MCP-only database runnable.
do $$
begin
  if to_regclass('public.onepager_narratives') is not null then
    execute 'grant select (run_id, student_id, trimester, model, narrative, created_at) '
            'on public.onepager_narratives to reporting_readonly';
    execute 'drop policy if exists reporting_readonly_select on public.onepager_narratives';
    execute 'create policy reporting_readonly_select on public.onepager_narratives '
            'for select to reporting_readonly using (true)';
  end if;
end $$;
