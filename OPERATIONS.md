# Operations & scaling notes

Operational assumptions and runbooks for the deployed Moodle Reports MCP
(Render web service → `https://moodle-mcp.tryrehearsal.ai`).

The production cutover sequence, new tables, required secrets, rollback, and 5,000-user gates are
maintained in [`docs/SECURITY_SCALABILITY_RELEASE.md`](docs/SECURITY_SCALABILITY_RELEASE.md).

## Deployment
- **Host:** Render web service `srv-da61ppjncjis73aer1hg`, branch `main`, auto-deploy on.
  A merge to `main` ships to production automatically.
- **Boot is fail-closed:** `validate_config()` runs at startup and refuses to boot on
  missing Supabase config, malformed `MCP_TOKENS`, a weak token (<24 chars, unless
  `ALLOW_WEAK_TOKENS`), or a bad `expires` format.

## Database credential (least privilege)
- The MCP reads via `SUPABASE_DATA_KEY`. In production this is a
  **`reporting_readonly`** JWT (SELECT-only role — see
  `sql/2026-08-26_reporting_readonly_role.sql`), **not** a full `service_role` key.
- Because a custom-role JWT is rejected by Supabase's API gateway as the `apikey`,
  `SUPABASE_ANON_KEY` (public) is set as the gateway apikey and the role JWT rides as
  the PostgREST bearer (`supabase_client._client()`). Do **not** unset `SUPABASE_ANON_KEY`
  while the DB key is a custom-role JWT, or every query will 401.
- The server logs a warning at boot if it detects a full `service_role` key still in use.

## Rate limiting — shared production state
The limiters in `security.py` use Redis when `MCP_REDIS_URL` is set:
- `MCP_RATE_LIMIT` (default 90) — per-token, per-window (via `GuardMiddleware`).
- `MCP_IP_RATE_LIMIT` (default 1200) — per-IP, pre-auth (via `TransportGuard`).

Redis makes budgets global across Render instances. A bounded in-process limiter remains active
during Redis failure, but its budget is instance-local; alert on Redis errors and treat sustained
fallback as degraded protection.

## Availability
The production blueprint uses a paid always-on instance. `/health` returns only
`{"status":"ok"}` — no data, version, or secret-state fingerprint.

## Tokens
- Faculty access tokens live in `MCP_TOKENS` (JSON map: token → `{name, campuses, expires?}`).
  `campuses:null` = all campuses (Programme Office).
- Each token may carry an optional `expires` (ISO date/datetime) for revoke-by-date without
  a redeploy. Prefer setting one (e.g. end of term) and rotating on a schedule.
- To revoke immediately: remove the entry from `MCP_TOKENS` in Render and redeploy.

## Runbook — rotate the DB key
1. Mint/obtain the new key.
2. Update `SUPABASE_DATA_KEY` (and `SUPABASE_ANON_KEY` if the role model changes)
   in Render → the service redeploys.
3. Verify: `GET /health` → 200; a `marks_overview` call returns data; boot log shows the
   expected DB key role (no `service_role` warning if using `reporting_readonly`).

## Runbook — check it's healthy
```bash
curl -s https://moodle-mcp.tryrehearsal.ai/health          # {"status":"ok"}
# tokenless MCP call must be rejected:
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://moodle-mcp.tryrehearsal.ai/mcp                    # 401
```

## Keep-warm — measured reality (2026-09-01)
GitHub schedules `*/10` crons best-effort and throttles them hard on low-activity
repos: over 2026-08-31 → 09-01 the keep-warm workflow actually ran ~5 times in 21
hours (gaps of 3-8 hours), so the free instance still spins down and faculty still
hit ~30-60s cold starts (observed as 9-22s 401/504 responses mid-morning). If cold
starts matter, use an external pinger (UptimeRobot / cron-job.org, 5-min interval,
same unauthenticated `/health`) or move the service to a paid always-on plan. The
Action stays as a harmless backstop.

## Env hygiene
- With OAuth enabled, `MCP_TOKENS` / `MCP_ADMIN_TOKEN` are never consulted on
  `/mcp` (FastMCP rejects foreign bearers first). Remove them from Render so they
  are not live secrets sitting unused in env.
- `OAUTH_DEFAULT_CAMPUSES=all` (an explicit unsafe override) + an empty faculty registry means
  every verified `jaipuria.ac.in` Google account not found in the student roster —
  including alumni and other non-faculty accounts — can read every campus's marks. The server now
  logs a boot warning for this combination; the faculty-only configuration is
  `OAUTH_DEFAULT_CAMPUSES=none` plus explicit `MCP_FACULTY` entries.

## Faculty access at scale — the mcp_faculty registry (2026-09-02)

Access for ~500 faculty is governed by the Supabase table `mcp_faculty`
(email PK, `campuses` = `"all"` or `["noida","jaipur",...]`, `active`,
`name`, `note`). The server consults it on every sign-in with the grant order:

1. `MCP_FACULTY` env — break-glass admin override (survives DB outages and a
   poisoned roster; keep ONLY the administrator here);
2. **student hard deny** — 3,144 students share the `jaipuria.ac.in` Google
   domain, so any email found in the `students` roster is denied even if it has
   an mcp_faculty row;
3. `mcp_faculty` row (active) — the normal path; malformed/inactive rows deny;
4. `OAUTH_DEFAULT_CAMPUSES` — `none` in production, so everything else denies.

Lookups are cached ~60s (roster hits 10 min), so changes apply within a minute
without a redeploy; a transient DB error serves the last-known-good grant for
signed-in users and denies strangers (fail closed).

**Add one faculty member** (service role, SQL editor):
```sql
insert into mcp_faculty (email, name, campuses, note)
values ('prof@jaipuria.ac.in', 'Prof Name', '["noida"]'::jsonb, 'added by <you>')
on conflict (email) do update
  set name=excluded.name, campuses=excluded.campuses,
      active=true, updated_at=now();
```

**Bulk-load from CSV** — stage and merge:
```sql
create temp table fac_in (email text, name text, campuses text);
-- \copy fac_in from 'faculty.csv' csv header   (psql) or paste INSERTs
insert into mcp_faculty (email, name, campuses, note)
select lower(trim(email)), trim(name),
       case when lower(trim(campuses)) in ('', 'all') then '"all"'::jsonb
            else to_jsonb(string_to_array(lower(replace(campuses,' ','')), '+')) end,
       'bulk load ' || current_date
from fac_in
on conflict (email) do update
  set name=excluded.name, campuses=excluded.campuses,
      active=true, updated_at=now();
```
(CSV `campuses` column: `all`, or `noida+jaipur` style.)

**Revoke**: `update mcp_faculty set active=false, updated_at=now() where email='...';`
— takes effect within the 60s cache TTL. Never delete rows for audit history.

**Invariants**: the MCP's own DB role (`reporting_readonly`) can SELECT this
table and cannot write it (verified: INSERT → permission denied). Students are
denied at gate 2 regardless of table contents. `create_report` inputs are
validated to `[A-Za-z0-9_-]{1,64}` before touching the HMAC-authenticated report-job
URL, so no faculty token can steer that request to another route.

**NAT headroom**: faculty may share campus egress IPs. Keep proxy headers disabled until the raw
origin is locked to the edge, use edge rate limiting for source IPs, and size the transport cap
from load-test evidence. Per-principal Redis limits still bound each account.

## OAuth sessions now survive deploys (2026-09-02)

FastMCP's OAuth state (client registrations, token mappings, refresh metadata)
is persisted in Supabase table `mcp_oauth_kv` via `oauth_storage.py`, replacing
the ephemeral disk default — so a deploy/restart no longer logs anyone out.
Every value is Fernet-encrypted with an independent `OAUTH_STORAGE_ENCRYPTION_KEY`: a DB leak
yields ciphertext only. The dedicated `mcp_oauth_writer` role has CRUD on this one table and no
access to student data.

**If you rotate `OAUTH_STORAGE_ENCRYPTION_KEY`**: migrate rows with both keys during a maintenance
window, or clear `mcp_oauth_kv` and require one re-login. Rotating `OAUTH_JWT_SIGNING_KEY` remains
independent. Expired rows are purged by the retention job (with opportunistic cleanup as backup).
