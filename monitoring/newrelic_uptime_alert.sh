#!/usr/bin/env bash
# Provision New Relic uptime monitoring + alerting for the Moodle MCP, with NO change
# to the MCP itself: an external Synthetics ping monitor on /health, an alert policy, a
# failure condition, and an email workflow. EU data centre (account 8495484).
#
# Usage:
#   NEW_RELIC_USER_API_KEY='NRAK-...' ALERT_EMAIL='ops@jaipuria.ac.in' \
#     bash monitoring/newrelic_uptime_alert.sh
#
# Env (with defaults):
#   NEW_RELIC_USER_API_KEY   required — a USER key (NRAK-...), not the license/ingest key
#   NEW_RELIC_ACCOUNT_ID     default 8495484
#   ALERT_EMAIL              required — where incidents are sent
#   MCP_HEALTH_URL           default https://moodle-mcp.tryrehearsal.ai/health
#   NR_LOCATION              default AWS_AP_SOUTH_1 (Mumbai, closest to Render/users)
#
# Re-running creates duplicates — this is a one-time bootstrap. Verify in
# one.eu.newrelic.com afterwards. NerdGraph shapes can drift between NR releases; if a
# mutation errors, the printed response names the bad field.
set -euo pipefail

: "${NEW_RELIC_USER_API_KEY:?set NEW_RELIC_USER_API_KEY (NRAK-... user key)}"
: "${ALERT_EMAIL:?set ALERT_EMAIL}"
ACC="${NEW_RELIC_ACCOUNT_ID:-8495484}"
HEALTH="${MCP_HEALTH_URL:-https://moodle-mcp.tryrehearsal.ai/health}"
LOC="${NR_LOCATION:-AWS_AP_SOUTH_1}"
API="https://api.eu.newrelic.com/graphql"   # EU account -> EU NerdGraph

gql () {  # $1 = graphql string; returns the JSON `data` (or prints errors and exits)
  local resp
  resp=$(curl -sS -X POST "$API" \
    -H "Content-Type: application/json" \
    -H "API-Key: ${NEW_RELIC_USER_API_KEY}" \
    --data "$(python3 -c 'import json,sys;print(json.dumps({"query":sys.argv[1]}))' "$1")")
  if printf '%s' "$resp" | grep -q '"errors"'; then
    echo "NerdGraph error:" >&2; printf '%s\n' "$resp" >&2; exit 1
  fi
  printf '%s' "$resp"
}

jqget () { python3 -c 'import json,sys;d=json.load(sys.stdin);print(eval("d"+sys.argv[1]))' "$1"; }

echo "==> 1/4 alert policy"
POLICY_ID=$(gql "mutation {
  alertsPolicyCreate(accountId: ${ACC}, policy: {
    name: \"Moodle MCP\", incidentPreference: PER_CONDITION
  }) { id }
}" | jqget "['data']['alertsPolicyCreate']['id']")
echo "    policyId=${POLICY_ID}"

echo "==> 2/4 Synthetics ping monitor on ${HEALTH}"
MON=$(gql "mutation {
  syntheticsCreateSimpleMonitor(accountId: ${ACC}, monitor: {
    name: \"moodle-mcp /health\",
    uri: \"${HEALTH}\",
    period: EVERY_5_MINUTES,
    status: ENABLED,
    locations: { public: [\"${LOC}\"] },
    advancedOptions: { responseValidationText: \"ok\", redirectIsFailure: true }
  }) { monitor { id guid } errors { type description } }
}")
printf '    %s\n' "$(printf '%s' "$MON" | jqget "['data']['syntheticsCreateSimpleMonitor']")"

echo "==> 3/4 alert condition — health check failing (>=1 FAILED in 5m)"
gql "mutation {
  alertsNrqlConditionStaticCreate(accountId: ${ACC}, policyId: ${POLICY_ID}, condition: {
    name: \"MCP /health failing\",
    enabled: true,
    nrql: { query: \"SELECT count(*) FROM SyntheticCheck WHERE monitorName = 'moodle-mcp /health' AND result = 'FAILED'\" },
    signal: { aggregationWindow: 300, aggregationMethod: EVENT_FLOW, aggregationDelay: 120, fillOption: STATIC, fillValue: 0 },
    terms: [{ threshold: 0, operator: ABOVE, priority: CRITICAL, thresholdDuration: 300, thresholdOccurrences: AT_LEAST_ONCE }]
  }) { id name }
}" >/dev/null && echo "    condition created"

echo "==> 4/4 email destination + workflow"
DEST_ID=$(gql "mutation {
  aiNotificationsCreateDestination(accountId: ${ACC}, destination: {
    name: \"Moodle MCP ops email\", type: EMAIL,
    properties: [{ key: \"email\", value: \"${ALERT_EMAIL}\" }]
  }) { destination { id } }
}" | jqget "['data']['aiNotificationsCreateDestination']['destination']['id']")
CHAN_ID=$(gql "mutation {
  aiNotificationsCreateChannel(accountId: ${ACC}, channel: {
    name: \"Moodle MCP email\", type: EMAIL, destinationId: \"${DEST_ID}\", product: IINT,
    properties: [{ key: \"subject\", value: \"[Moodle MCP] {{ issueTitle }}\" }]
  }) { channel { id } }
}" | jqget "['data']['aiNotificationsCreateChannel']['channel']['id']")
gql "mutation {
  aiWorkflowsCreateWorkflow(accountId: ${ACC}, createWorkflowData: {
    name: \"Moodle MCP\", workflowEnabled: true, mutingRulesHandling: NOTIFY_ALL_ISSUES,
    destinationConfigurations: [{ channelId: \"${CHAN_ID}\" }],
    issuesFilter: { name: \"policy\", type: FILTER, predicates: [
      { attribute: \"labels.policyIds\", operator: EXACTLY_MATCHES, values: [\"${POLICY_ID}\"] } ] }
  }) { workflow { id } }
}" >/dev/null && echo "    workflow wired to ${ALERT_EMAIL}"

echo "Done. Verify in one.eu.newrelic.com → Alerts → Policies → 'Moodle MCP'."
