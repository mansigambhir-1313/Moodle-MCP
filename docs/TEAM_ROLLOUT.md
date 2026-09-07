# Codex team rollout

This runbook promotes the Moodle MCP from a developer-installed plugin to a managed Jaipuria
workspace plugin. It assumes the production endpoint remains
`https://moodle-mcp.tryrehearsal.ai/mcp` and the GitHub default branch is `main`.

## Release gate

1. Merge the reviewed `codex/moodle-codex-compat` pull request into `main`. Do not push directly
   to `main`; Render auto-deploys the merge.
2. Require the `ci / test` check on the pull request. It installs the pinned/runtime
   dependencies, checks them, validates both Codex bundles and the marketplace, and runs every
   isolated repository test script.
3. Wait for Render's `/health` deployment check, then run the GitHub Actions `live-smoke`
   workflow manually. It verifies health, OAuth discovery, PKCE/refresh-token metadata, and the
   unauthenticated 401 challenge without using a faculty account.
4. From a fresh Codex desktop task, install the plugin and complete one real Google OAuth flow.
   Call `whoami`, one read tool, and one denied out-of-campus request. This user-session check is
   the final gate because automated CI must not hold a faculty Google session.

## Import the managed marketplace

A ChatGPT/Codex workspace admin should:

1. Open **Admin settings → Plugins → Add → Import marketplace**.
2. Use repository URL `https://github.com/mansigambhir-1313/Moodle-MCP`.
3. Leave **Path** empty: the repository manifest is at `.agents/plugins/marketplace.json`.
4. Select branch `main` after the release pull request is merged.
5. Import `jaipuria-ai-labs`, set Moodle Faculty Analytics to **Available** (or
   **Installed by default** for the pilot group), and require authentication on install.
6. Assign access to a two-person pilot role first, then expand the role after the user-session
   gate passes. Workspace policy is authoritative; policy values checked into this repository are
   defaults for local installs only.

The plugin declares `.mcp.json`, so it is a **Codex desktop-only plugin** even though the MCP
server itself is remote HTTPS. Teammates must use the Codex desktop app and start a new task after
installation so the MCP tools are loaded.

## Faculty authorization checklist

Before enabling a teammate, confirm their lower-cased Jaipuria email has an active, explicit
campus grant in `mcp_faculty`. Production must keep `OAUTH_DEFAULT_CAMPUSES=none`; an unlisted
Workspace account is denied. For each pilot user:

- `whoami` returns the expected email and campus list;
- a read call for an allowed campus succeeds;
- an out-of-campus call returns no data;
- `create_report` is tested only if the report-agent credentials are configured and the pilot is
  expected to have that write capability.

## Rollback

If authentication or scoping fails, the workspace admin should set the plugin to **Not available**
while the server owner rolls Render back to the last known-good commit. Set affected
`mcp_faculty.active` rows to `false` for an immediate access revocation. Do not relax
`OAUTH_DEFAULT_CAMPUSES` as a workaround.

## Current known limits

- The complete browser consent/token exchange still needs the pilot user-session gate after every
  material OAuth change.
- OpenAI workspace identity restrictions cannot independently inspect this server's user email
  until an OIDC discovery/UserInfo contract is added; the server-side verified-email, domain, and
  faculty-grant checks remain mandatory.
- Horizontal scaling requires shared/edge rate limiting. The current in-process limits assume a
  single Render instance.
