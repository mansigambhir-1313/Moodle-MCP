# Moodle MCP — gap analysis (what's required/claimed vs what's actually enforced)

Companion to `PRODUCTION_READINESS.md`. That doc plans the work; this one is an honest
audit of **gaps** — where the shipped/enforced reality diverges from the AIA-1012 epic
acceptance criteria, the security model, or the "record everything for 5,000 users"
goal. Grounded in the code as of `2e0482c` (2026-09-21). Ordered by severity.

Legend: **REQ** = requirement/expectation · **NOW** = shipped/enforced today · **GAP**.

---

## 🔴 CRITICAL

### G1. No role-based access — every Jaipuria ID (incl. ~2,800 students) has all-campus, faculty-level read + report generation
- **REQ** (AIA-1012/1013/1016): "faculty/student/admin tool permissions, **deny-by-default**, immediate revocation, prove **allowed AND denied** journeys, per-tool RBAC through the gateway."
- **NOW**: `security.principal_from_claims` short-circuits any `jaipuria.ac.in` (+ subdomain) account to `campuses=None` (**all campuses**) *before* the student-roster deny — so the deny applies only to non-Jaipuria domains. Verified: students (who are `@jaipuria.ac.in`) get the same all-campus grant as faculty. `whoami` exposes no role; there is no faculty/student/admin distinction in the MCP.
- **GAP**: (a) **Privacy exposure** — any of ~2,800 students can read any other student's marks/attendance across all 5 campuses, and can `create_report` for anyone. (b) **Diverges from the epic**: the shipped model is the opposite of deny-by-default; there are **no denied journeys to demonstrate** for Jaipuria IDs, which Vaibhav's 5 Oct adversarial review explicitly checks. The gateway (AIA-1013) that would *enforce* RBAC is built but **not deployed**.
- **This is a deliberate data-owner choice ("open to all for now")** — but it must be reconciled with the epic before launch: either (i) formally rescope the epic's RBAC acceptance, or (ii) add student self-scoping (in-MCP: detect roster email → scope to own record) and/or deploy the gateway for per-tool RBAC. Until then, treat student PII cross-exposure as a known, accepted risk in writing.

---

## 🟠 HIGH

### G2. Recording is not actually live (AIA-1210)
- **REQ**: user/email-attributable traces of every MCP call, retained with defined access.
- **NOW**: capture *code* shipped; but the `mcp_audit` backend was **never applied to prod** (only `mcp_oauth_kv` exists), `SUPABASE_AUDIT_KEY` + `MCP_CAPTURE_*` are unset, and `render.yaml` capture-ON is uncommitted.
- **GAP**: zero durable recording today. Enablement is staged (`scripts/RECORDING_ENABLEMENT.md`) but unexecuted (ops). Until done, AIA-1210's "who connected, when, what" cannot be shown.

### G3. Recording student PII with no consent mechanism and unscheduled retention
- **REQ**: lawful, governed capture of identity + which student records each user viewed + returned marks.
- **NOW**: `mcp_audit.consents` table exists in the migration but **no code reads/writes it**; the 180-day `purge_expired_mcp_security_data()` exists but is **not scheduled** (no pg_cron); no privacy notice.
- **GAP**: no consent flow, no automatic retention enforcement, no published notice — for a highly sensitive joined dataset (who-viewed-whom + marks) covering minors-adjacent student data at scale.

### G4. Hosting + scale not launch-ready
- **NOW**: live Render service still on the **free plan** (spin-down) despite `render.yaml: standard`; `MCP_REDIS_URL` unset (in-process rate limiter diverges across instances); Google `tokeninfo` called per request (now traced via `mcp.auth`, but **not cached/mitigated**); no load test; `create_report` has **no per-user cost/quota cap** (any user can drive model spend generating reports for any student).
- **GAP**: cannot serve 5,000 concurrently; horizontal scaling breaks rate limiting; auth latency + model cost unbounded.

### G5. Report short-links may expose student PII if leaked
- **REQ** (AIA-1355): "another user cannot retrieve an artifact by guessing or replaying its URL."
- **NOW**: short-links are SHA-256-hashed + expiring (good vs guessing). But it's unverified whether the link endpoint requires **authentication** or is public-with-expiry.
- **GAP**: if the report short-link is publicly resolvable (unauth) within its TTL, a leaked/forwarded link exposes that student's full report to anyone until expiry. **Verify the link endpoint's auth**; if public, that's a PII exposure to reconcile. The guess/replay adversarial test is also not yet executed.

---

## 🟡 MEDIUM

### G6. Test coverage is unit/mock-only — no integration, RLS, or load tests
- **NOW**: 15 plain-assert files, all mocked. The campus-scoping AND-filter, `mcp_audit` RLS (admin-only reads), the SECURITY DEFINER RPC, and the OAuth/DCR flow are verified only by **reasoning against mocks**, never against a real (test) Supabase/PostgREST or a running server.
- **GAP**: no proof that RLS actually blocks a non-admin read of `mcp_audit`; no end-to-end auth test; no gateway-routing test (gateway undeployed); no concurrency/load test. First real CI run will only exercise the unit suite.

### G7. JChat integration incomplete
- **GAP**: (a) the `X-Request-Id` correlation header is a documented, **uncommitted draft** in the JChat repo — prompts aren't yet joinable to tool calls (Rajika). (b) `OAUTH_ALLOW_CROSS_CLIENT_PKCE` defaults off → the Claude.ai connector's cross-node token exchange can fail (likely re-auth-pain contributor). (c) `jaipuriaschools.ac.in` is admitted by JChat as USER but **denied by the MCP** (not a subdomain of `jaipuria.ac.in`) — graceful-denial UX unverified; decision pending.

### G8. "Record everything" is only half — prompts aren't captured MCP-side
- **NOW**: the MCP records tool calls + args + results. The user's **prompts/questions live only in JChat** (Mongo/Langfuse).
- **GAP**: without the G7(a) correlation, "everything they did" can't be reconstructed end-to-end; oversized (>256 KB) result payloads are truncated (no S3 offload built).

### G9. Eval not trustworthy yet (AIA-1380)
- **NOW**: reports are accurate; the Scheme-1 faithfulness panel produces false flags; fixes are in the working tree, **uncommitted** (per data-owner direction).
- **GAP**: `overall_pct` is an unvalidated triage signal until the panel is fixed + calibrated against human labels; no fleet-wide accuracy sweep.

---

## 🟢 LOW / hygiene

- **G10. Config drift**: `render.yaml` is documentation-only for the manually-created live service (env doesn't sync) — repo intent and live env can silently diverge; no reconciliation check.
- **G11. Release gate**: everything has been pushed straight to `main`; CI was just added and hasn't yet gated a PR. No documented rollback plan.
- **G12. Admin surfaces undeployed**: the admin policy MCP (AIA-1016) and reference integrations (AIA-1014) exist locally but aren't deployed, so policy authorship + revocation can't be demonstrated.

---

## The one to decide first
**G1** is both a live privacy exposure and the item that most directly contradicts the epic's stated acceptance criteria (and Vaibhav's 5 Oct review). Everything else is executable ops/eng work already planned; G1 is a **decision**: accept student cross-exposure in writing and rescope the RBAC acceptance, or add student self-scoping / deploy the gateway before the 5,000-user launch.
