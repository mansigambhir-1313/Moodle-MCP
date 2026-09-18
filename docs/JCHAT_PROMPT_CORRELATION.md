# JChat ↔ MCP prompt correlation (the "what they asked" half)

The MCP records every **tool call + arguments + result** (mcp_audit). The user's actual
**prompt** is never sent to the MCP — it lives in JChat (MongoDB messages + Langfuse
traces + New Relic). To get the full picture — *what the user asked → which tools ran →
what came back* — the two sides just need a shared id to join on.

## The change (config-only — no LibreChat fork)

JChat already supports the per-turn placeholders `{{LIBRECHAT_BODY_MESSAGEID}}` and
`{{LIBRECHAT_BODY_CONVERSATIONID}}` in `mcpServers` headers (resolved in
`packages/api/src/utils/env.ts`). Our MCP already reads `X-Request-Id` into its audit
`request_id`. So stamp each MCP call with the JChat message id.

In **`librechat.yaml`** and **`librechat.railway.yaml`**, extend the `moodle` server:

```yaml
  moodle:
    type: streamable-http
    url: "https://moodle-mcp.tryrehearsal.ai/mcp"
    timeout: 60000
    requiresOAuth: true
    serverInstructions: true
    headers:
      X-Request-Id: "{{LIBRECHAT_BODY_MESSAGEID}}"
      X-Conversation-Id: "{{LIBRECHAT_BODY_CONVERSATIONID}}"
```

Result: `mcp_audit.tool_calls.request_id` == the JChat `messageId`. Join that to the
JChat message (Mongo `messages` collection / Langfuse trace) to see the prompt and the
model's reply alongside the exact tool calls, arguments, and results the MCP recorded.

> Verify custom `headers` are sent alongside `requiresOAuth` (they should be additive to
> the injected OAuth bearer). If a build strips them on OAuth servers, the one-line
> fallback is to add `X-Request-Id` in the MCP client's request builder
> (`packages/api` MCP call path) from the active message id.

## What JChat already captures (no new work)
- **Prompts + responses**: Mongo `messages` (durable) and **Langfuse** traces
  (`LANGFUSE_*` fanout is wired) — full conversation content + tool-call spans.
- **Usage/cost/identity**: New Relic `[Usage]` events + `JChatUser/Balance/Transaction`
  (with `MONGO_SYNC_ENABLED=true`), winston logs keyed by `userId`/`requestId`.

## What to add for durable analytics (optional, small)
A compact per-turn event so activity is queryable without scanning Mongo — emit one
New Relic custom event (JChat already has `packages/api/src/newrelic/`): `{ userId,
email, conversationId, messageId, model, toolsInvoked[], mcpServers[], promptChars,
completionChars, tokens }`. Keep **prompt text** in Mongo/Langfuse, not New Relic.

## Privacy
Correlating prompts to the student records a user pulled makes this a sensitive joined
dataset. Note it in the privacy policy; the JChat security audit already flagged
`MONGO_SYNC_INCLUDE_EMAIL` — keep raw email out of New Relic there too. See
`docs/DATA_CAPTURE_PLAN.md`.
