# Jaipuria Moodle Reports MCP

A **faculty-facing, read-only** Model Context Protocol server over the Jaipuria
`student-report-system` data. Connect it to any MCP host (a dashboard, Claude.ai, ChatGPT,
Claude CLI) and ask, in plain language, about student performance reports, cohort analytics, and
**report accuracy** — the whole pipeline output, queryable.

> Design lineage: the [Rehearsal MCP](https://github.com/JaipuriaAILabs/rehearsal-mcp) patterns
> (read-only, bounded caches, routing-contract tools, response budgets), adapted from a
> per-student RLS model to a **role-based, campus-scoped faculty model**. Full design in
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Data-first

The **primary** surface is the raw Moodle data — every ingested student's marks, attendance,
subjects and enrolment, queryable directly from the `students` / `courses` / `marks` /
`attendance_sessions` tables (works whether or not a report was generated). The generated reports
and their two-scheme accuracy scores are a **secondary** layer on top.

## Tools (18, across 6 modules)

| Module | Tools | Source |
|---|---|---|
| **students** (primary) | `list_students`, `get_student`, `student_marks`, `student_attendance` | raw students / marks / attendance |
| **subjects** (primary) | `list_subjects`, `subject_performance` | raw courses / marks / attendance |
| **insights** ⭐ (primary) | `student_trajectory`, `student_360`, `cohort_pulse`, `watchlist` | cross-trimester + single-pane |
| **analytics** (primary) | `marks_overview`, `attendance_overview`, `top_performers`, `cohort_compare` | raw rollup |
| **at_risk** (primary) | `at_risk_students`, `attendance_watch`, `zero_alerts` | raw rollup |
| accuracy (secondary) | `get_report_accuracy`, `accuracy_overview`, `flagged_reports` | `report_accuracy` |
| reports (secondary) | `get_student_report`, `report_pipeline_status` | `student_reports` |
| _(server)_ | `whoami` | token |

Every tool is `SELECT`-only, campus-scoped to the caller's token, and returns bounded, structured
rows. No write path, no ingestion, no mailing — ever.

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SUPABASE_* and an MCP_ADMIN_TOKEN
uvicorn server:app --port 8000
# health:
curl localhost:8000/health
```

Connect a host to `http://localhost:8000/mcp` with header `Authorization: Bearer <your token>`.

## Access model

- A **bearer token** identifies a faculty principal and its **allowed campuses**.
- `MCP_ADMIN_TOKEN` → a single all-campus token. `MCP_TOKENS` → a JSON map of
  `token → {name, campuses}` (`campuses: null` = all). A caller can never widen scope beyond its
  grant; a requested campus is intersected with the granted set.
- The Supabase **service role key stays server-side** and is never exposed to the host.

## Deploy

- **Render:** `render.yaml` (Python 3.12, `/health` check). Set the env vars in the dashboard.
- **Docker:** `docker build -t moodle-mcp . && docker run -p 8000:8000 --env-file .env moodle-mcp`
- Point a custom domain at it; connect hosts to `https://<domain>/mcp`.

## Safety invariants

Read-only forever · campus-scoped every query · uniform `found:false` misses · secret stripping
(no run ids / storage keys leave the server) · response budgets + paging · graceful degradation
(never 500 the turn) · bounded caches only (OOM-safe). See `docs/ARCHITECTURE.md` §11.
