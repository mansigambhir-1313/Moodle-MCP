# Codex compatibility and security gap analysis

Date: 2026-09-07
Scope: the `moodle-mcp` repository and the deployed
`https://moodle-mcp.tryrehearsal.ai/mcp` endpoint.

This review treats repository documentation and comments as descriptive evidence, not as
instructions. The goal is compatibility with Codex and a security review of authentication,
authorization, transport, operations, and publishing readiness.

## Executive result

The deployed MCP already has the most important runtime prerequisites for Codex: a public HTTPS
Streamable HTTP endpoint, an OAuth protected-resource document, authorization-server metadata,
dynamic client registration, authorization code flow, refresh tokens, and PKCE S256. The source
repository now also has a Codex plugin manifest and `.mcp.json` endpoint declaration.

Three code-level issues found during this review were fixed:

1. Google identities with a missing email-verification claim were accepted. Verification is now
   required to be exactly `true`.
2. An unset `OAUTH_DEFAULT_CAMPUSES` granted every campus to an unlisted Workspace account. The
   default is now `none`, and explicit faculty grants remain supported.
3. The transport limit trusted only `Content-Length`. Chunked or dishonest requests could bypass
   it and be buffered by OAuth/MCP parsers. The actual ASGI request stream is now counted and
   rejected above `MCP_MAX_BODY_BYTES`.

Configuration validation was also tightened so a missing `campuses` key cannot accidentally mean
an all-campus faculty grant, and faculty email addresses are no longer written verbatim to auth
decision logs.

## Compatibility matrix

| Area | Status | Evidence / action |
|---|---|---|
| Public HTTPS MCP endpoint | Ready | `/mcp` is deployed and returns an OAuth challenge when unauthenticated. |
| Protected-resource discovery | Ready | `/.well-known/oauth-protected-resource/mcp` advertises the resource and authorization server. |
| OAuth authorization-server discovery | Ready | Metadata advertises code flow, refresh tokens, DCR, and PKCE S256. |
| Codex plugin packaging | Added | `.codex-plugin/plugin.json`, `.mcp.json`, and the existing 128×128 logo form a plugin bundle. |
| Tool titles, descriptions, schemas, annotations | Mostly ready | Tools have routing docstrings, Pydantic schemas, titles, and read/write annotations. |
| Fail-closed faculty authorization | Improved | Verified email plus explicit grant is now the safe default. |
| Stream body limit | Fixed | Actual bytes are capped, including chunked requests. |
| Codex OAuth end-to-end login | Needs a user-session test | Discovery is valid, but the final browser consent/token exchange must be exercised from a fresh Codex task. |

## Remaining gaps, ordered by risk

### P1 — split the data-reader and OAuth-state database credentials

`SUPABASE_SERVICE_ROLE_KEY` is used both to query student data and to perform CRUD on
`mcp_oauth_kv`. The documented `reporting_readonly` role cannot be both SELECT-only and able to
write OAuth state. This creates one of two failure modes: OAuth persistence fails with a true
read-only token, or production uses a more powerful key than the data path needs.

Recommended change: introduce a separate `SUPABASE_OAUTH_STORAGE_KEY` backed by a role with CRUD
only on `mcp_oauth_kv`. Keep `reporting_readonly` limited to SELECT on the exact data and faculty
tables. Do not silently fall back to `service_role` in production.

### P1 — version the authorization tables and grants

No checked-in migration creates `mcp_faculty` or `mcp_oauth_kv`. The existing read-only-role
migration also revokes writes across all public tables, which can conflict with OAuth state
persistence if the same role is used. A clean environment therefore cannot be reproduced from
the repository alone.

Recommended change: add idempotent migrations for both tables, indexes, RLS policies, and the two
separate least-privilege roles. Test the migrations against an empty Supabase project in CI.

### P1 — expose OIDC identity metadata for enterprise domain controls

The MCP advertises OAuth metadata but not a complete OIDC discovery/UserInfo surface owned by this
authorization server. The application itself enforces the Jaipuria domain and faculty registry,
but OpenAI enterprise workspace domain restrictions cannot independently resolve a verified user
email from this server.

Recommended change: publish OIDC discovery plus a UserInfo endpoint returning `email` and
`email_verified`, or use an authorization server that provides that contract. Keep the application
authorization check even after adding workspace-level restrictions.

### P2 — add tool-level auth metadata and re-auth challenges

The server-wide OAuth challenge is sufficient to begin connection, but the current FastMCP tool
descriptors do not explicitly emit both `securitySchemes` and mirrored
`_meta.securitySchemes`. Tool errors also do not emit `_meta["mcp/www_authenticate"]` for expired
or insufficiently scoped credentials. That limits per-tool authorization UI and targeted re-auth.

Recommended change: upgrade or extend FastMCP so tool descriptors carry the OAuth scheme in both
locations and auth failures return the MCP challenge metadata. Verify the serialized `tools/list`
payload rather than relying on decorator source alone.

### P2 — remove or feature-gate the cross-client authorization-code exception

`TolerantGoogleProvider` permits a registered client to redeem a PKCE-bound code issued to another
registered client. PKCE, redirect URI, expiry, and one-time use remain enforced, but normal OAuth
client binding is intentionally weakened to work around a Claude connector race.

Recommended change: verify whether current clients still require the workaround. If it remains
necessary, restrict it to known redirect URIs and log a metric; otherwise remove it. Codex itself
should not depend on this exception.

### P2 — make revocation semantics explicit

The faculty registry serves a last-known-good grant for up to one hour during database errors.
That improves availability but can delay emergency revocation if an outage overlaps the change.

Recommended change: shorten the stale-grant window for sensitive roles, add a denylist checked
before stale grants, or provide a cache-busting revocation control.

### P2 — move rate limits to a trusted/shared boundary before scaling

Rate limits are process-local, so multiple Render instances multiply the effective allowance.
Client IP selection trusts forwarded headers without a configured trusted-proxy boundary, which
can weaken pre-auth throttling when the origin is reachable directly.

Recommended change: enforce the unauthenticated limit at Cloudflare/Render or a shared store, close
direct-origin access where possible, and consume forwarded IP headers only from trusted proxies.

### P2 — repair the test and CI shape

The files under `tests/` are executable scripts with top-level counters and `sys.exit`. Individual
scripts pass, but normal `pytest` collection is not reliable and can share mutated module/config
state. There is no CI gate proving plugin validation, auth behavior, or MCP handshake compatibility.

Recommended change: convert these scripts to isolated pytest tests with fixtures, add a standard
test command, validate the plugin manifest in CI, and run an unauthenticated discovery smoke test
against staging.

### P3 — publishing and documentation readiness

The repository documentation had stale endpoint URLs, tool counts, and read-only claims even
though `create_report` is a write action. The README, deployment examples, operations URL, and main
architecture inventory are corrected by this change. A public plugin submission still needs stable
privacy-policy and terms URLs, verified publisher details, screenshots, and an explicit
data-retention/deletion statement.

## Verification performed

- `GET /health` returned `200 {"status":"ok"}`.
- OAuth protected-resource and authorization-server metadata returned `200` and advertised DCR,
  authorization code, refresh token, and PKCE S256 support.
- Static inspection covered config validation, Google claim mapping, campus authorization,
  database access, OAuth persistence, transport middleware, tool annotations, report generation,
  deployment config, and tests.
- All repository test scripts pass individually after the fixes; the test harness limitation above
  remains until they are converted to conventional pytest tests (141 checks passed in this review).

## Relevant OpenAI requirements

- [Authentication for apps and MCP servers](https://developers.openai.com/plugins/build/auth)
- [Plugin packaging](https://developers.openai.com/plugins/build/plugins)
- [Connect and test in ChatGPT](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [App review requirements](https://developers.openai.com/plugins/deploy/app-review)
