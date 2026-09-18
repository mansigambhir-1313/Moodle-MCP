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

## Add once the MCP emits telemetry (P1.2 / P2.1)
The MCP currently ships **no OTel/APM**. After adding an OTel exporter to
`otlp.eu01.nr-data.net` (per-tool spans + metrics), add these NRQL conditions to the
same policy — they need in-app data and are inert until then:

| Condition | NRQL (sketch) | Threshold |
|-----------|---------------|-----------|
| 5xx / error-rate spike | `SELECT percentage(count(*), WHERE error IS true) FROM Span WHERE service.name='jaipuria-moodle-mcp'` | > 5% for 5m |
| p95 tool latency | `SELECT percentile(duration.ms, 95) FROM Span WHERE service.name='jaipuria-moodle-mcp' AND span.kind='server'` | > 3000 ms for 5m |
| throughput floor / silence | `SELECT rate(count(*), 1 minute) FROM Span WHERE service.name='jaipuria-moodle-mcp'` | no data 10m |
| auth failure surge | `SELECT count(*) FROM Span WHERE name='mcp.auth' AND error IS true` | > N for 5m |

## Usage analytics (from the audit ledger, not NR)
Per-user activity, most-queried students, tool-usage mix, adoption/DAU come from the
Supabase audit ledger — query `mcp_audit.v_activity` (see `docs/DATA_CAPTURE_PLAN.md`).
Keep raw student PII in that locked schema; do **not** mirror it into New Relic.

> NerdGraph mutation shapes drift between NR releases; if the script errors, the printed
> response names the offending field. Re-running creates duplicates — it's a one-time
> bootstrap; manage changes in the NR UI afterward.
