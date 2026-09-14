-- MCP activity capture — query ergonomics for the widened audit metadata (AIA-1012).
-- Apply with the Supabase migration role, never an application credential.
--
-- CONTEXT: the MCP already WRITES full activity into mcp_audit.tool_calls.metadata
-- (jsonb) when the MCP_CAPTURE_* flags are on — identity.email/name/campuses,
-- arguments, result, source_ip. No schema change is needed to *store* it. This
-- migration only makes that jsonb efficient to *query* and safe to *govern*:
--   1. a GIN index so identity/argument lookups don't scan partitions,
--   2. a flattened read view for analysts,
--   3. an explicit, tunable retention note.
-- It is additive and idempotent; it changes no existing column and drops nothing.

-- 1. Index the metadata blob (jsonb_path_ops = compact, fast for @> containment,
--    e.g. metadata @> '{"identity":{"email":"x@jaipuria.ac.in"}}').
create index if not exists mcp_tool_calls_metadata_gin
  on mcp_audit.tool_calls using gin (metadata jsonb_path_ops);

-- 2. Analyst-facing flattened view. Reads the captured fields out of metadata so
--    queries never hand-parse jsonb. Inherits the base table's RLS (locked to the
--    audit role); grant SELECT only to a reviewer role you control.
create or replace view mcp_audit.v_activity as
select
  event_id,
  occurred_at,
  tool_name,
  outcome,
  error_code,
  duration_ms,
  request_id,
  user_subject,                                   -- HMAC pseudonym (always present)
  session_subject,
  client_subject,
  campus_scope,
  metadata #>> '{identity,email}'   as user_email,      -- real id (capture_identity)
  metadata #>> '{identity,name}'    as user_name,
  metadata #>  '{identity,campuses}' as user_campuses,
  metadata ->> 'source_ip'          as source_ip,        -- (capture_client_ip)
  metadata ->  'arguments'          as arguments,        -- (capture_arguments)
  metadata ->  'result'             as result,           -- (capture_results)
  metadata ->> 'server_version'     as server_version
from mcp_audit.tool_calls;

comment on view mcp_audit.v_activity is
  'Flattened MCP tool activity. user_email/arguments/result/source_ip are populated '
  'only for calls recorded while the matching MCP_CAPTURE_* flag was on. Contains '
  'student PII and who-viewed-whom — restrict SELECT and honour the retention policy.';

-- 3. OPTIONAL fast-filter columns (uncomment if email/IP filtering becomes hot).
--    Generated columns are maintained by Postgres from metadata; safe on the
--    partitioned parent (PG12+). Kept commented so the default footprint stays lean.
-- alter table mcp_audit.tool_calls
--   add column if not exists user_email text
--     generated always as (metadata #>> '{identity,email}') stored,
--   add column if not exists source_ip text
--     generated always as (metadata ->> 'source_ip') stored;
-- create index if not exists mcp_tool_calls_email_time_idx
--   on mcp_audit.tool_calls (user_email, occurred_at desc);

-- 4. RETENTION. purge_expired_mcp_security_data() already deletes tool_calls older
--    than 180 days. The widened rows now carry PII + result payloads, so decide
--    deliberately: keep 180d for parity, shorten for the payload-bearing rows, or
--    lengthen for compliance. To shorten ONLY the heavy payloads while keeping the
--    lean event, run a periodic scrub (example — strip payloads after 30 days but
--    keep the event + identity):
-- update mcp_audit.tool_calls
--   set metadata = metadata - 'arguments' - 'result'
--   where occurred_at < now() - interval '30 days'
--     and (metadata ? 'arguments' or metadata ? 'result');
