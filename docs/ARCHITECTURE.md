# Jaipuria Moodle MCP — Technical Architecture

> Deep-dive reference for the **faculty-facing, campus-scoped** Moodle Reports MCP server.
> Exposes student performance data and deterministic cohort analytics through 26 query/status tools, plus
> one explicitly annotated action for on-demand report generation.
> Design lineage: the Rehearsal MCP (read-only, bounded, routing-contract tools) — adapted from a
> per-student RLS model to a **role-based, campus-scoped faculty model**.

## 1. System context

The server sits between an MCP host (Codex, ChatGPT, Claude, a faculty dashboard, or CLI) and the
`student-report-system/1.0.0` Supabase project. Query tools return structured rows scoped to the
faculty caller's allowed campuses; the host LLM frames and summarises the results.

```mermaid
flowchart LR
    subgraph Host["MCP host (Codex / ChatGPT / Claude / dashboard)"]
        LLM["Host LLM"]
    end
    subgraph Server["moodle-mcp (Render, uvicorn)"]
        FastMCP["FastMCP app (/mcp)<br/>OAuth or static bearer gate"]
        Tools["tools/* (7 modules, 27 tools)"]
        Guard["guardrails.py"]
        Svc["MoodleService<br/>(read-only, pooled)"]
    end
    subgraph Supabase["Supabase (shared report project)"]
        Catalog["student_reports (catalog)<br/>narrative + evidence_packet + figures"]
        Accuracy["report_accuracy<br/>two-scheme validation scores"]
        Jobs["student_report_jobs<br/>pipeline status"]
        Source["students · marks · attendance · courses"]
        Bucket["Storage `student-reports`<br/>rendered HTML/PDF"]
    end

    LLM -- "MCP over HTTPS + OAuth bearer" --> FastMCP
    FastMCP --> Tools --> Guard
    Tools --> Svc --> Catalog
    Svc --> Accuracy
    Svc --> Jobs
    Svc --> Source
    Tools -- "report link" --> Bucket
```

Sibling systems on the same project:
- **`moodle-agent`** — the pipeline that *writes* everything this server reads (ingestion → calc →
  narrative → validation → `student_reports` / `report_accuracy`).
- Query tools are read-only. `create_report` delegates generation to `moodle-agent` over
  authenticated HTTPS; the MCP never ingests or mails.

## 2. Why this MCP is different (and exclusive)

It is not a generic table browser. It exposes three things no raw DB view gives a dashboard:

1. **Finished, validated reports** — the human-readable narrative + the deterministic figures the
   renderer used, already joined (`student_reports.evidence_packet` + `narrative`).
2. **Validated report context** — generated report and `student_360` responses can carry the
   pipeline's accuracy context alongside the deterministic evidence used to build the narrative.
3. **Early-warning analytics** — `at_risk_students`, `attendance_watch`, `zero_alerts` turn raw
   marks/attendance into the exact triage a programme office acts on.

## 3. Tenant-isolation model (role-based, campus-scoped)

Unlike the student MCP (per-user RLS on `auth.uid()`), this server serves faculty who see
*institutional* data for their campuses. Boundaries:

1. **Bearer access gate** — production uses Google OAuth through FastMCP; static deployments may
   use `MCP_TOKENS`. Verified Google identities are mapped through explicit `MCP_FACULTY` or
   `mcp_faculty` grants. No valid grant means deny.
2. **Server-side scoping** — every tool applies `.in_("campus", allowed_campuses)` (or `.eq` for a
   single-campus token). A caller can never widen scope by passing a campus they aren't granted;
   requested campus is intersected with the granted set.
3. **Read-only query credential** — the Supabase data credential lives only server-side. Query
   tools are `SELECT`-only. The one action calls `moodle-agent` with separate server credentials.
4. **Secret stripping** — `strip_secrets()` removes storage object keys, raw tokens, and internal
   ids from every projected row. Rendered-report access is via a short-lived signed URL, never a
   raw path.

Supporting rules (`guardrails.py`): UUID/id pre-validation → clean miss (no Postgres `22P02`
leak); uniform `{"found": false}` for missing-vs-out-of-scope (no existence oracle); soft-deleted
/ non-`ready` rows excluded from student-facing report reads.

## 4. Runtime stack

| Layer | Choice | Notes |
|---|---|---|
| MCP framework | `fastmcp >= 2.14, < 3` | Tool registration, HTTP transport |
| ASGI | Starlette (`mcp.http_app()`) + uvicorn | `GET /health` prepended for Render |
| DB client | `supabase-py >= 2.5` (PostgREST) | Read-only service role, server-side only |
| Validation | pydantic v2 + pydantic-settings | Typed tool params + env config |
| HTTP | httpx | OAuth-state persistence and the report-generation service |
| Caches | in-process bounded `TTLCache` | No unbounded module dict, ever (OOM invariant) |

Single process with bounded in-memory data caches; encrypted OAuth client/token mappings may be
persisted in Supabase so sessions survive deploys.

## 5. Data-access layer (`supabase_client.py`)

`MoodleService` — thin, read-only, pooled:
- Constructed once per process with the service key; **one `Client`** reused (report data is not
  per-user, so unlike the student MCP no per-user pool is needed — a single bounded client).
- `allowed_campuses` is attached per request from the verified token; every query helper applies it.
- Aggregation helpers use `count="exact"` head queries and explicit pagination — PostgREST caps
  result rows at 1000, so counts/analytics never rely on a single unpaged `select` (a lesson baked
  in from the pipeline).

## 6. Tool layer (`tools/`)

### 6.1 Module contract (same shape as Rehearsal)
```python
class XxxParams(BaseModel): ...        # host-facing field descriptions
def _impl(svc, ...) -> dict: ...       # pure logic, testable with a FakeSvc
def register(mcp, get_service):
    @mcp.tool(annotations=READONLY_ANNOTATIONS)
    async def tool_name(params: XxxParams) -> dict:
        svc = await get_service()      # access-checked, campus-scoped
        return _impl(svc, ...)
```
Conventions: **routing-contract docstrings** (`WHAT / USE WHEN / DO NOT USE / RETURNS`),
`READONLY_ANNOTATIONS` (auto-approvable), param models over loose kwargs, response-size budgets.

### 6.2 Module × tool inventory

| Module | Tools | Primary sources |
|---|---|---|
| `students.py` | `list_students`, `get_student`, `student_marks`, `student_attendance` | roster, marks, attendance |
| `subjects.py` | `list_subjects`, `subject_performance`, `section_compare`, `assessment_breakdown`, `subject_difficulty` | courses, enrolments, marks |
| `insights.py` | `campus_performance_report`, `declining_students`, `student_trajectory`, `student_360`, `cohort_pulse`, `watchlist` | combined data rollups |
| `analytics.py` | `marks_overview`, `attendance_overview`, `top_performers`, `cohort_compare` | students, marks, attendance |
| `at_risk.py` | `at_risk_students`, `attendance_watch`, `zero_alerts` | marks, attendance |
| `reports.py` | `get_student_report`, `report_data_availability` | reports and source data |
| `actions.py` | `create_report`, `get_report_job` | signed durable `moodle-agent` queue |
| *(server.py)* | `whoami` | authenticated principal |

## 7. Response-size budgets & paging

Token discipline is a contract (shared constants in `guardrails.py`):
- `LIST_PREVIEW_CHARS = 400` — list cards carry a bounded preview, never the full narrative.
- Full report bodies only behind `get_student_report` (paged narrative sections).
- Lists cap at `MAX_LIST = 50` with `next_offset` / `has_more`.
- Students addressed by name + enrolment id; internal `run_id` / object keys never leave the server.

## 8. Caching & memory invariants (OOM-safe)

| Cache | Bound | Purpose |
|---|---|---|
| `_run_cache` | `TTLCache(64 × 300s)` | latest `final` run_id per (campus,batch) |
| `_rollup_cache` | `TTLCache(8 × 300s)` | per-run aggregate rollups |
| `_marks_cache` | `TTLCache(8 × 300s)` | bounded raw-mark pages used by rollups |
| `_grants` / `_students` | bounded TTL caches | faculty grants and student hard-deny lookups |

## 9. Configuration (`config.py`)

| Group | Vars |
|---|---|
| Supabase | `SUPABASE_URL`, split data/OAuth/audit JWTs, `SUPABASE_ANON_KEY` |
| Access | Google OAuth + explicit faculty registry; static fallback via `MCP_TOKENS` / `MCP_ADMIN_TOKEN` |
| Identity | `MCP_SERVER_NAME`, `MCP_SERVER_VERSION`, `MCP_SERVER_BASE_URL` |
| Reports | `REPORT_PUBLIC_BASE_URL` (for `get_student_report` links), `STORAGE_BUCKET` |

`validate_config()` is a fail-closed boot check. All logging → stderr; log lines never contain
token contents or PII.

## 10. Deployment & publishing

- **Render web service** (`render.yaml`): Python 3.12, `uvicorn server:app --port $PORT`.
- **`Dockerfile`**: `python:3.12-slim`, non-root user, mirrors render.
- Public endpoint: `https://moodle-mcp.tryrehearsal.ai/mcp`; `GET /health` for platform checks.
- Hosted clients use OAuth discovery and PKCE; static-token mode is a separate fallback deployment.

## 11. Design invariants (checklist for new tools)
1. Query tools remain read-only; actions are explicit, narrowly scoped, and correctly annotated.
2. Campus-scope every query with the token's allowed set; intersect requested campus.
3. Validate ids; map failure to `not_found()`; uniform `found:false`.
4. `strip_secrets()` every row; object keys / run_ids never leave the server.
5. Lists within budget + `next_offset`; full bodies only behind a paged get.
6. Degrade gracefully (`available:false`, notes) on missing table / empty scope — never 500.
7. Any new cache bounded (`TTLCache`).
8. Use the correct read/write annotations plus a `WHAT / USE WHEN / DO NOT USE / RETURNS` docstring.
