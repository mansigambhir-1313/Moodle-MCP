# Monitoring & alerting — Moodle MCP

New Relic **EU**, account **8495484** (the MCP-platform observability account; distinct
from JChat's NR account 8379209). NerdGraph endpoint: `https://api.eu.newrelic.com/graphql`.

## Available now (no MCP change): external uptime
`newrelic_uptime_alert.sh` provisions, via NerdGraph:
- a **Synthetics ping monitor** on `https://moodle-mcp.tryrehearsal.ai/health` every 5 min
  (validates the body contains `ok`, treats redirects as failure),
- an **alert policy** "Moodle MCP",
- a **condition** that fires when the check FAILS,
- an **email workflow** to `ALERT_EMAIL`.

```bash
NEW_RELIC_USER_API_KEY='NRAK-...' ALERT_EMAIL='ops@jaipuria.ac.in' \
  bash monitoring/newrelic_uptime_alert.sh
```
This catches the failure modes that have actually bitten us — the service being down /
spun down (free-plan cold starts) and boot failures — without instrumenting the app.

## Per-tool APM (once NEW_RELIC_LICENSE_KEY is set)
The MCP now ships an OTel exporter (`telemetry.py`) that emits one SERVER span per tool
call — `mcp.tool.<name>`, `service.name='jaipuria-moodle-mcp'`, attributes `mcp.tool` /
`mcp.outcome` / `mcp.error_code` / `mcp.campus_scope`, ERROR status on failure. Set
`NEW_RELIC_LICENSE_KEY` in Render to turn it on, then add these NRQL conditions to the
"Moodle MCP" policy (inert until spans arrive):

| Condition | NRQL | Threshold |
|-----------|------|-----------|
| error-rate spike | `SELECT percentage(count(*), WHERE otel.status_code='ERROR') FROM Span WHERE service.name = 'jaipuria-moodle-mcp' AND name LIKE 'mcp.tool.%'` | > 5% for 5m |
| p95 tool latency | `SELECT percentile(duration.ms, 95) FROM Span WHERE service.name = 'jaipuria-moodle-mcp' AND name LIKE 'mcp.tool.%'` | > 3000 ms for 5m |
| throughput floor / silence | `SELECT rate(count(*), 1 minute) FROM Span WHERE service.name = 'jaipuria-moodle-mcp' AND name LIKE 'mcp.tool.%'` | no data 10m |
| auth-denial surge | `SELECT count(*) FROM Span WHERE service.name = 'jaipuria-moodle-mcp' AND name = 'mcp.auth' AND mcp.outcome = 'failure'` | > N for 5m |
| auth p95 latency | `SELECT percentile(duration.ms, 95) FROM Span WHERE service.name = 'jaipuria-moodle-mcp' AND name = 'mcp.auth'` | > 1500 ms for 5m (Google tokeninfo per request) |

## Usage analytics (from the audit ledger, not NR)
Per-user activity, most-queried students, tool-usage mix, adoption/DAU come from the
Supabase audit ledger — query `mcp_audit.v_activity` (see `docs/DATA_CAPTURE_PLAN.md`).
Keep raw student PII in that locked schema; do **not** mirror it into New Relic.

> NerdGraph mutation shapes drift between NR releases; if the script errors, the printed
> response names the offending field. Re-running creates duplicates — it's a one-time
> bootstrap; manage changes in the NR UI afterward.
