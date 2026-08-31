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
