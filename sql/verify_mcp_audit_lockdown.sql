-- Repeatable proof that the mcp_audit ledger is admin-only (AIA-1012 / GAP G6).
-- Run in the Supabase SQL editor (project sadbfvfcmmxgtatfjfmc). Expected results noted.
-- Verified 2026-09-22: every "expect false" below returned false; RLS on for all base
-- tables; even mcp_audit_writer cannot SELECT (it only EXECUTEs the SECURITY DEFINER RPC).

-- 1) PostgREST-reachable roles cannot even enter the schema -> no table is reachable.
select has_schema_privilege('anon','mcp_audit','usage')          as anon_schema_usage,   -- expect false
       has_schema_privilege('authenticated','mcp_audit','usage') as auth_schema_usage;   -- expect false

-- 2) Per-table: RLS enabled on base tables; no SELECT for anon/authenticated; the writer
--    role is write-only (no SELECT) — it records via the RPC but cannot read the ledger.
select
  t.tablename,
  c.relrowsecurity                                                       as rls_on,
  has_table_privilege('anon',            'mcp_audit.'||t.tablename,'select') as anon_sel,   -- expect false
  has_table_privilege('authenticated',   'mcp_audit.'||t.tablename,'select') as auth_sel,   -- expect false
  has_table_privilege('mcp_audit_writer','mcp_audit.'||t.tablename,'select') as writer_sel  -- expect false
from pg_tables t
join pg_class c on c.oid = ('mcp_audit.'||t.tablename)::regclass
where t.schemaname='mcp_audit'
order by t.tablename;

-- 3) The writer CAN execute the record RPC (write path) — expect true.
select has_function_privilege('mcp_audit_writer',
  'public.record_mcp_tool_call(uuid,text,text,text,text,text,integer,text,text,text,jsonb)',
  'execute') as writer_can_write;

-- Optional defense-in-depth (not required — parent RLS + no grants + no schema usage
-- already lock the partitions): enable RLS on each partition too.
--   alter table mcp_audit.tool_calls_2026_q3 enable row level security;
--   alter table mcp_audit.tool_calls_2026_q4 enable row level security;
--   alter table mcp_audit.tool_calls_2027_h1 enable row level security;
--   alter table mcp_audit.tool_calls_default enable row level security;
