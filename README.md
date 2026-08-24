# Jaipuria Moodle Reports MCP

A **faculty-facing, read-only Model Context Protocol (MCP) server** that makes the Jaipuria
`student-report-system` data queryable in plain language. Connect it to any MCP host (a dashboard,
Claude.ai, ChatGPT, Claude CLI) and ask about student marks, attendance, subjects, cohort
analytics, longitudinal trends, at-risk students, and report accuracy — every ingested student,
scoped to the caller's campuses.

**Live:** `https://moodle-mcp-f6do.onrender.com/mcp` · **Health:** `/health` · **Tools:** 27
**Repo:** `github.com/mansigambhir-1313/Moodle-MCP` · **Owner:** Jaipuria AI Labs

---

## Overview

The pipeline in [`moodle-agent`](../moodle-agent) ingests Moodle data, computes analytics, and
generates validated student reports into a Supabase project. This MCP is the **read side** of that
project for faculty and the programme office: it exposes the raw data and the pipeline's outputs as
~27 structured, auto-approvable tools that a host LLM routes on.

It is **data-first** — the primary surface is the raw gradebook and attendance (queryable for
*every* student, report or not); the generated reports and their two-scheme accuracy scores are a
secondary layer. It is **read-only forever**: no tool writes, ingests, or emails.

Design lineage: the [Rehearsal MCP](https://github.com/JaipuriaAILabs/rehearsal-mcp) patterns
(bounded caches, routing-contract docstrings, response budgets, secret stripping, graceful
degradation), adapted from that server's per-student RLS model to a **role-based, campus-scoped
faculty model**.

### Where it fits

- **Upstream:** the shared `student-report-system` Supabase project (tables `students`, `courses`,
  `enrolments`, `marks`, `attendance_sessions`, `student_reports`, `report_accuracy`), written by
  `moodle-agent`.
- **Downstream:** any MCP host — a faculty dashboard, Claude.ai / ChatGPT connectors, Claude CLI.

---

## What makes it exclusive

- **Longitudinal, not just snapshot** — one run holds every trimester (T1–T6). Tools like
  `student_trajectory` and `declining_students` catch a student sliding term-over-term, which a
  point-in-time query never shows.
- **Single-pane views** — `student_360` and `cohort_pulse` return a whole student / whole cohort in
  one call, ready for a dashboard drawer or landing screen.
- **Accuracy as first-class data** — every generated report carries a two-scheme validation score
  (faithfulness panel + two-turn LLM judge). Ask *"which reports are flagged and why?"*
- **Teaching & curriculum signals** — `section_compare` (A-vs-B fairness), `assessment_breakdown`
  (quiz vs assignment vs project), `subject_difficulty` (curriculum pressure points).

---

## Tools (27)

Every tool is `SELECT`-only, campus-scoped to the caller's token, bounded, and carries a
`WHAT / USE WHEN / DO NOT USE / RETURNS` routing docstring.

### Students — raw data (primary)
| Tool | What it returns |
|---|---|
| `list_students` | Roster for a campus/batch (± section), every ingested student |
| `get_student` | One student's complete record — per-subject component marks + attendance |
| `student_marks` | Flat, component-level gradebook rows for a student |
| `student_attendance` | Per-subject attendance (present / sessions / %) for a student |

### Subjects — raw data (primary)
| Tool | What it returns |
|---|---|
| `list_subjects` | Subjects/courses for a scope, with trimester, sections, enrolment |
| `subject_performance` | A subject's cohort marks, pass rate, attendance, per-component means |
| `section_compare` | Section-vs-section means + spread (teaching/marking signal) |
| `assessment_breakdown` | Cohort performance by assessment kind (quiz/assignment/project…) |
| `subject_difficulty` | Subjects ranked hardest-first (pass rate + zeros) |

### Insights — longitudinal & single-pane (hero)
| Tool | What it returns |
|---|---|
| `student_trajectory` | A student's marks/attendance trend across trimesters + label |
| `student_360` | One-call student view: percentile rank, trend, risk flags, accuracy |
| `cohort_pulse` | One-call cohort KPIs: marks, attendance, pass rate, at-risk, distribution |
| `watchlist` | Auto intervention list — reasons + suggested action, ranked |
| `declining_students` | Cohort-wide biggest term-over-term mark drops (early warning) |

### Analytics & at-risk (primary)
| Tool | What it returns |
|---|---|
| `marks_overview` | Cohort marks snapshot — mean, pass rate, distribution, zeros |
| `attendance_overview` | Cohort attendance — mean, counts below 75% / 65% |
| `top_performers` | Highest overall marks in a scope |
| `cohort_compare` | Campus-vs-campus means for a batch |
| `at_risk_students` | Composite risk ranking (zeros + attendance + failing marks) |
| `attendance_watch` | Students below an attendance threshold |
| `zero_alerts` | Students with a recorded zero (most urgent) |

### Reports & accuracy (secondary)
| Tool | What it returns |
|---|---|
| `get_report_accuracy` | One report's two-scheme accuracy score + interpretation |
| `accuracy_overview` | Cohort accuracy — mean %, verified / drift / flagged |
| `flagged_reports` | The human-review queue (validation-flagged reports) |
| `get_student_report` | The generated narrative report for a student |
| `report_pipeline_status` | Ready / held / failed counts for a scope |
| `whoami` | The caller's principal and allowed campuses |

See [`docs/INNOVATION_ROADMAP.md`](docs/INNOVATION_ROADMAP.md) for Phase-3 ideas
(`attendance_eligibility`, `attendance_marks_link`, `anomalies`, `roster_health`).

---

## Quickstart

### Connect a host (deployed server)
```bash
claude mcp add moodle --transport http https://moodle-mcp-f6do.onrender.com/mcp \
  --header "Authorization: Bearer <your MCP_TOKENS value>"
```
Then ask, in plain language:
> "cohort pulse for jaipur 2024-26" · "who's declining" · "build my watchlist" ·
> "show JJ24PG001's full record" · "hardest subjects" · "compare sections of Wealth Management"

### Run locally
```bash
cd moodle-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in the vars below
uvicorn server:app --port 8899
curl localhost:8899/health      # {"status":"ok",...}
```

### Smoke test (real MCP handshake + live queries)
```bash
MCP_URL="http://localhost:8899/mcp" MCP_TOKEN="<a token>" python test_client.py
```

---

## Configuration

`config.py` (pydantic-settings, reads `.env` + env vars). `validate_config()` is a fail-closed
boot check on the Supabase vars.

| Variable | Description | Where to get it |
|---|---|---|
| `SUPABASE_URL` | Report project URL (`https://sadbfvfcmmxgtatfjfmc.supabase.co`) | Supabase → Settings → API |
| `SUPABASE_SERVICE_ROLE_KEY` | Read service key (server-side only, never exposed) | Supabase → Settings → API · also in `moodle-agent/.env` |
| `MCP_TOKENS` | JSON map of faculty tokens → `{name, campuses}` (see below) | You generate it |
| `MCP_ADMIN_TOKEN` | Single all-campus break-glass token (alternative to `MCP_TOKENS`) | You generate it |
| `REPORT_PUBLIC_BASE_URL` | Base for report links (default `https://reports.tryrehearsal.ai`) | — |
| `MCP_SERVER_BASE_URL` | Public URL of this service (optional) | Render dashboard |

**All logging goes to stderr; log lines never contain token contents or PII.**

---

## Access model (role-based, campus-scoped)

Unlike the student MCP (per-user RLS), this serves faculty who see *institutional* data for their
campuses. A **bearer token** maps to a principal with an allowed-campus set; every tool intersects
the requested campus with that set. A campus outside the grant returns `{"found": false}` — no data
leaks.

Generate a per-campus token block:
```bash
python3 -c "import secrets; print('mcp_'+secrets.token_urlsafe(24))"   # one per faculty
```
```jsonc
// MCP_TOKENS (single-line JSON in the env var)
{
  "mcp_...indore": {"name": "Indore TNP",       "campuses": ["indore"]},
  "mcp_...office": {"name": "Programme Office",  "campuses": null}      // null = all campuses
}
```
The Supabase **service-role key stays server-side** and is never handed to the host. There is no
write path in the codebase.

---

## Architecture

```
MCP host (dashboard / Claude / ChatGPT)
        │  MCP over HTTP + Bearer <faculty token>
        ▼
server.py (FastMCP /mcp, /health)
  get_authenticated_service()  → verify token → MoodleService(allowed_campuses)
        │
  tools/* (6 modules, 27 tools) — each: Params model + _impl(svc,…) + register()
        │  every query .in_("campus", allowed) ; strip_secrets ; response budgets
        ▼
Supabase (read service role) — students · courses · enrolments · marks ·
                               attendance_sessions · student_reports · report_accuracy
```

Full design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### Key files
| Path | Purpose |
|---|---|
| `server.py` | FastMCP app, `whoami`, `/health`, auth dependency, tool wiring |
| `config.py` | Settings + `validate_config()` |
| `supabase_client.py` | Read-only `MoodleService`, campus scoping, run resolution |
| `tools/common.py` | Shared helpers: `courses_for`, `marks_for`, `cohort_rollup`, caches |
| `tools/students.py` · `subjects.py` · `insights.py` | Primary data tools |
| `tools/analytics.py` · `at_risk.py` | Cohort rollups |
| `tools/accuracy.py` · `reports.py` | Secondary report layer |
| `cache.py` · `guardrails.py` · `annotations.py` | TTL cache, budgets/scoping, tool hints |
| `test_client.py` | End-to-end MCP client smoke test |

### Caches (OOM-safe — bounded `TTLCache` only)
`_run_cache` (latest final run per scope), `_rollup_cache` / `_marks_cache` (cohort raw-data
rollups). Cohort tools page past PostgREST's 1000-row cap and cache the result for 5 min.

---

## Deployment

- **Render** (`render.yaml` blueprint or Docker): Python 3.12 / Docker, `uvicorn server:app`,
  health check `/health`. Set `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `MCP_TOKENS` in the
  dashboard.
- **Docker:** `docker build -t moodle-mcp . && docker run -p 8000:8000 --env-file .env moodle-mcp`
- Current prod is on the **Free** instance (spins down after ~15 min idle → ~50s cold start).
  Upgrade to Starter for always-on.

| Environment | URL | Notes |
|---|---|---|
| Production | `https://moodle-mcp-f6do.onrender.com` | Free instance, `main` auto-deploys |
| Local | `http://localhost:8899` | `uvicorn server:app --port 8899` |

Full test/deploy steps: [`DEPLOY.md`](DEPLOY.md).

---

## Runbooks

**Rotate access tokens** — regenerate `MCP_TOKENS` (same generator), update the Render env var; the
service restarts and old tokens stop working. Re-issue the new tokens to faculty.

**Add a per-campus faculty** — add one `"mcp_...": {"name": "...", "campuses": ["<campus>"]}` entry
to `MCP_TOKENS`, redeploy, hand them their token.

**Add a new tool** — follow `docs/ARCHITECTURE.md` §11: add a `Params` model + `_impl(svc,…)` +
`register()`, campus-scope every query, `strip_secrets`, write the routing docstring, register in
`server.py`. Reuse the raw-data helpers in `tools/common.py`.

**Cold start / first request slow** — Free instance woke from idle (~50s). Warm it with
`curl <url>/health`, or upgrade the instance.

**Verify a deploy** — `curl <url>/health`, then
`MCP_URL="<url>/mcp" MCP_TOKEN="<token>" python test_client.py`.

---

## Safety invariants

Read-only forever · campus-scope every query · uniform `{"found": false}` misses (no existence
oracle) · secret stripping (run ids / storage keys / hashes / emails never leave the server) ·
service-role key server-side only · response budgets + paging · graceful degradation (never 500 the
turn) · bounded caches only (OOM-safe). Detail in `docs/ARCHITECTURE.md` §3, §11.
