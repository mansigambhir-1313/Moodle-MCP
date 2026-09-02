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
