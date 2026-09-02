# Operations & scaling notes

Operational assumptions and runbooks for the deployed Moodle Reports MCP
(Render web service → `https://moodle-mcp-f6do.onrender.com`).

## Deployment
- **Host:** Render web service `srv-da61ppjncjis73aer1hg`, branch `main`, auto-deploy on.
  A merge to `main` ships to production automatically.
- **Boot is fail-closed:** `validate_config()` runs at startup and refuses to boot on
  missing Supabase config, malformed `MCP_TOKENS`, a weak token (<24 chars, unless
  `ALLOW_WEAK_TOKENS`), or a bad `expires` format.

## Database credential (least privilege)
- The MCP reads via `SUPABASE_SERVICE_ROLE_KEY`. In production this is a
  **`reporting_readonly`** JWT (SELECT-only role — see
  `sql/2026-08-26_reporting_readonly_role.sql`), **not** a full `service_role` key.
- Because a custom-role JWT is rejected by Supabase's API gateway as the `apikey`,
  `SUPABASE_ANON_KEY` (public) is set as the gateway apikey and the role JWT rides as
  the PostgREST bearer (`supabase_client._client()`). Do **not** unset `SUPABASE_ANON_KEY`
  while the DB key is a custom-role JWT, or every query will 401.
- The server logs a warning at boot if it detects a full `service_role` key still in use.

## Rate limiting — single-instance assumption
The limiters in `security.py` are **in-process**:
- `MCP_RATE_LIMIT` (default 90) — per-token, per-window (via `GuardMiddleware`).
- `MCP_IP_RATE_LIMIT` (default 240) — per-IP, pre-auth (via `TransportGuard`).

These are correct **on a single instance**. If the service is ever scaled to
**multiple instances** (Render horizontal scaling), each instance keeps its own
counters, so the *effective* limit becomes `N × limit` and a client could exceed the
intended budget by hitting different instances. This is acceptable for the current
single-instance free/starter deployment. **If you scale out**, move the limiter state to
a shared store (e.g. Redis via `INCR`+`EXPIRE`, or Supabase) so the budget is global,
or enforce limits at an upstream edge/CDN instead.

## Keep-warm (cold starts)
The Render free tier spins the instance down when idle, so the first request after a lull
takes ~30–60s. `.github/workflows/keep-warm.yml` pings the unauthenticated `/health`
every ~10 minutes to keep it warm. Alternatives: a paid Render instance (no spin-down), or
an external uptime monitor. `/health` returns only `{"status":"ok"}` — no data, no token —
so the public ping is safe.

## Tokens
- Faculty access tokens live in `MCP_TOKENS` (JSON map: token → `{name, campuses, expires?}`).
  `campuses:null` = all campuses (Programme Office).
- Each token may carry an optional `expires` (ISO date/datetime) for revoke-by-date without
  a redeploy. Prefer setting one (e.g. end of term) and rotating on a schedule.
- To revoke immediately: remove the entry from `MCP_TOKENS` in Render and redeploy.

## Runbook — rotate the DB key
1. Mint/obtain the new key.
2. Update `SUPABASE_SERVICE_ROLE_KEY` (and `SUPABASE_ANON_KEY` if the role model changes)
   in Render → the service redeploys.
3. Verify: `GET /health` → 200; a `marks_overview` call returns data; boot log shows the
   expected DB key role (no `service_role` warning if using `reporting_readonly`).

## Runbook — check it's healthy
```bash
curl -s https://moodle-mcp-f6do.onrender.com/health          # {"status":"ok"}
# tokenless MCP call must be rejected:
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://moodle-mcp-f6do.onrender.com/mcp                    # 401
```

## Keep-warm — measured reality (2026-09-01)
GitHub schedules `*/10` crons best-effort and throttles them hard on low-activity
repos: over 2026-08-31 → 09-01 the keep-warm workflow actually ran ~5 times in 21
hours (gaps of 3-8 hours), so the free instance still spins down and faculty still
hit ~30-60s cold starts (observed as 9-22s 401/504 responses mid-morning). If cold
starts matter, use an external pinger (UptimeRobot / cron-job.org, 5-min interval,
same unauthenticated `/health`) or move the service to a paid always-on plan. The
Action stays as a harmless backstop.

## Deploys log every user out (known limitation)
FastMCP's OAuth proxy stores dynamic client registrations, JTI mappings and
upstream tokens in an encrypted DiskStore under the app user's home directory —
which is ephemeral on Render. Every deploy or restart therefore invalidates all
issued tokens ("JTI mapping not found" → 401 invalid_token) and each connected
host must re-run Google sign-in. `OAUTH_JWT_SIGNING_KEY` keeps the JWTs
*verifiable* but not the JTI map, so it does not prevent this. Fix when it becomes
painful: pass a persistent `client_storage` (Redis, or a Postgres-backed
key-value store — the Supabase project itself can host the table) into the
provider, or accept re-login as the cost of a deploy.

## Env hygiene
- With OAuth enabled, `MCP_TOKENS` / `MCP_ADMIN_TOKEN` are never consulted on
  `/mcp` (FastMCP rejects foreign bearers first). Remove them from Render so they
  are not live secrets sitting unused in env.
- `OAUTH_DEFAULT_CAMPUSES=all` (the code default) + empty `MCP_FACULTY` means
  every verified `jaipuria.ac.in` Google account — students and alumni included,
  if they hold domain accounts — can read every campus's marks. The server now
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
validated to `[A-Za-z0-9_-]{1,64}` before touching the admin-authenticated
agent URL, so no faculty token can steer that request to another route.

**NAT headroom**: ~500 faculty on campus share egress IPs, so the pre-auth
per-IP limit is raised via `MCP_IP_RATE_LIMIT=1200` (per minute) on Render;
per-principal limits (90/min) still bound each individual account.
