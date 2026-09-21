# Moodle MCP — ops handoff checklist (to launch for ~5,000 Jaipuria users)

The single ordered runbook for the steps **only ops/humans can do** — the in-repo code
work is complete and merged (`5d87d11`), 16 test files green. Do these in order; each
has a done-check. 🔴 = hard blocker · 🟠 = do before wide launch · 🟢 = tuning.

Targets: Render service `jaipuria-moodle-mcp` (`moodle-mcp.tryrehearsal.ai`) · Supabase
**Moodle Data** `sadbfvfcmmxgtatfjfmc` · New Relic **EU** acct `8495484`.
Deeper detail: `PRODUCTION_READINESS.md`, `RECORDING_ENABLEMENT.md`, `GAP_ANALYSIS.md`.

---

## 1. Secrets & auth stability (do first — a wrong move here is the outage we already hit)
- [ ] 🔴 **Pin `OAUTH_STORAGE_ENCRYPTION_KEY` + `OAUTH_JWT_SIGNING_KEY`** as permanent Render env values — set once, never regenerate. (A changed encryption key orphaned every DCR client on 2026-09-18 → "client ID not found"; an unstable signing key logs everyone out each deploy.) To rotate later, use a comma-separated keyset `new,old` for a grace window (now supported).
- [ ] 🟠 **Set `OAUTH_ALLOW_CROSS_CLIENT_PKCE=true` and `OAUTH_REDIRECT_HOSTS=<claude.ai connector host>`** on this service — else the hosted Claude.ai connector can hit `mcp_token_exchange_failed` (likely the re-auth pain).
- [ ] 🟢 Confirm `REPORT_LINK_SECRET` (on `moodle-agent`) is set + stable — unset fails closed; rotating it invalidates all live report links.

## 2. Hosting (can't serve 5,000 on free)
- [ ] 🔴 **Move the live Render service off the free plan** to always-on standard; confirm it no longer spins down. Same for the **`moodle-agent`** report service.

## 3. Turn on recording (nothing is recorded today — `mcp_audit` was never applied)
Follow `scripts/RECORDING_ENABLEMENT.md`; in short:
- [ ] 🔴 **Apply** `sql/2026-09-18_mcp_audit_backend.sql` to `sadbfvfcmmxgtatfjfmc` (Supabase SQL editor / `apply_migration`). Done-check: `to_regclass('mcp_audit.tool_calls')` not null, `record_mcp_tool_call` exists, role `mcp_audit_writer` exists.
- [ ] 🔴 **Mint the audit credential**: `SUPABASE_JWT_SECRET=… python3 scripts/mint_audit_jwt.py` → set as **`SUPABASE_AUDIT_KEY`** in Render; set **`MCP_AUDIT_HMAC_KEY`** (`python3 -c "import secrets;print(secrets.token_urlsafe(32))"`).
- [ ] 🔴 **Commit + push the enablement bundle** (a human commits — mass-PII-capture is gated for the agent): `render.yaml` (capture flags), `sql/2026-09-18_mcp_audit_backend.sql`, `scripts/`. Then set in the Render dashboard (render.yaml doesn't sync to the manually-created service): `MCP_CAPTURE_IDENTITY/ARGUMENTS/RESULTS/CLIENT_IP=true`, `MCP_TRUST_PROXY_HEADERS=true`, `MCP_CAPTURE_ARGS_MAX_BYTES=32768`, `MCP_CAPTURE_RESULT_MAX_BYTES=262144`.
- [ ] 🟠 Set **`MCP_REQUIRE_AUDIT=true`** only *after* the two above are verified (fail-closed: refuses to serve a call it can't record; if the backend is missing it errors/boot-fails).
- [ ] Done-check: after a tool call, `select * from mcp_audit.v_activity order by occurred_at desc limit 5;` shows `user_email`, `arguments`, `result`, `source_ip`.

## 4. Scale & abuse limits
- [ ] 🔴 **Set `MCP_REDIS_URL`** — the per-principal tool limiter *and* the new `create_report` cost cap only hold across instances with a shared store; without it, horizontal scaling breaks both.
- [ ] 🟠 Raise `MCP_IP_RATE_LIMIT` for the trusted JChat egress **or** enable `GATEWAY_ENFORCED` (single ingress) — all JChat traffic egresses one Railway IP, so the per-IP cap is otherwise the ceiling for everyone.
- [ ] 🟢 Tune `MCP_CREATE_REPORT_LIMIT` (default 60/user/hour) if faculty legitimately generate larger batches.

## 5. Monitoring & alerting
- [ ] 🟠 Set **`NEW_RELIC_LICENSE_KEY`** (EU ingest key) → OTel tool + `mcp.auth` spans start flowing.
- [ ] 🟠 Run `NEW_RELIC_USER_API_KEY=… ALERT_EMAIL=… bash monitoring/newrelic_uptime_alert.sh` (uptime monitor + policy), then add the APM NRQL conditions from `monitoring/README.md`.

## 6. Report generation at scale
- [ ] 🟠 Confirm `AGENT_REPORT_QUEUE=true` + `AGENT_SHARED_SECRET` (≥32 chars) so generation is queued, not synchronous, under load.

## 7. Governance & privacy (required under the accepted all-access model)
- [ ] 🔴 **Publish a privacy notice** — every verified Jaipuria account (incl. students) can read any student's data and generate reports, and activity is logged with identity. Record this accepted decision (already on epic AIA-1012 / `GAP_ANALYSIS.md` G1).
- [ ] 🟠 **Schedule retention**: pg_cron `select public.purge_expired_mcp_security_data();` daily (180-day purge).
- [ ] 🟠 **Lock the `mcp_audit` schema** to admins only; keep raw PII out of New Relic.
- [ ] 🟢 Shorten `REPORT_LINK_TTL_DAYS` from 90 → 7–30 days for student-PII links.

## 8. JChat side (Rajika)
- [ ] 🟠 Add the correlation header to the `moodle` `mcpServers` entry in `librechat.yaml` + `librechat.railway.yaml`: `X-Request-Id: "{{LIBRECHAT_BODY_MESSAGEID}}"` (see `docs/JCHAT_PROMPT_CORRELATION.md`) so prompts join to tool-call audit rows.
- [ ] 🟢 Decide `jaipuriaschools.ac.in` handling (JChat admits as USER; MCP denies — confirm the graceful-denial UX is acceptable).

## 9. Verify & ramp
- [ ] Re-add the Moodle MCP connector in Claude.ai / JChat (a deploy drops the session); `whoami` returns `campuses: all`.
- [ ] Smoke: health 200, `/mcp` 401, a `get_student` by name, one `create_report`, then `v_activity` shows the rows.
- [ ] Ramp the cohort **150 → 500 → 5,000** with New Relic + audit dashboards watched; load-test the target concurrency first.

---
**Blocking set for a safe launch:** §1 (keys), §2 (hosting), §3 (recording), §4 (Redis), §7 privacy notice. Everything else is do-soon or tuning.
