"""Report retrieval — find students, open one full report, check pipeline status. Read-only."""
from pydantic import BaseModel, Field

from annotations import READONLY_ANNOTATIONS
from config import settings
from guardrails import clamp_limit, not_found, truncate
from tools.common import accuracy_rows, one_report, report_rows, student_label

_CARD = "student_id,full_name,first_name,campus,batch,trimester,status,above_average_count,strongest_subject,subjects_count,personal_pattern_kind"


class SearchParams(BaseModel):
    query: str | None = Field(default=None, description="name or enrolment-id fragment to match", max_length=120)
    campus: str | None = Field(default=None, description="campus filter (within your grant)", max_length=64)
    batch: str | None = Field(default=None, description="batch e.g. '2024-26'", max_length=64)
    trimester: str | None = Field(default=None, description="trimester number e.g. '5'", max_length=8)
    limit: int = Field(default=20, description="max cards, 1-50", ge=1, le=50)


class ReportParams(BaseModel):
    student_id: str = Field(description="enrolment id, e.g. 'JJ24PG099'", max_length=64)
    campus: str | None = Field(default=None, max_length=64)
    batch: str | None = Field(default=None, max_length=64)
    trimester: str | None = Field(default=None, max_length=8)


class StatusParams(BaseModel):
    campus: str = Field(description="campus", max_length=64)
    batch: str = Field(description="batch e.g. '2024-26'", max_length=64)
    trimester: str = Field(description="trimester number", max_length=8)


def _search_impl(svc, p: SearchParams) -> dict:
    rows = report_rows(svc, campus=p.campus, batch=p.batch, trimester=p.trimester, cols=_CARD)
    if p.query:
        ql = p.query.lower()
        rows = [r for r in rows if ql in (r.get("full_name") or "").lower()
                or ql in (r.get("student_id") or "").lower()]
    rows.sort(key=lambda r: (r.get("full_name") or r.get("student_id") or ""))
    rows = rows[:clamp_limit(p.limit)]
    cards = [{**student_label(r), "campus": r.get("campus"), "batch": r.get("batch"),
              "trimester": r.get("trimester"), "subjects": r.get("subjects_count"),
              "subjects_at_or_above_average": r.get("above_average_count"),
              "strongest_subject": r.get("strongest_subject")} for r in rows]
    return {"count": len(cards), "students": cards}


def _report_impl(svc, p: ReportParams) -> dict:
    row = one_report(svc, p.student_id, campus=p.campus, batch=p.batch, trimester=p.trimester)
    if not row:
        return not_found("report")
    narr = row.get("narrative") or {}
    pkt = row.get("evidence_packet") or {}
    subjects = pkt.get("subjects", [])
    att = pkt.get("attendance_summary", {})
    acc = accuracy_rows(svc, campus=row.get("campus"), batch=row.get("batch"),
                        trimester=row.get("trimester"),
                        cols="student_id,overall_pct,overall_label,agent_accuracy,panel_verdict")
    acc = next((a for a in acc if a.get("student_id") == p.student_id), None)
    actions = [{"title": a.get("title"), "instruction": a.get("instruction") or a.get("detail")}
               for a in (narr.get("actions") or [])[:3]]
    pp = narr.get("personal_pattern") or {}
    ast = narr.get("attendance_story") or {}
    return {
        "found": True,
        "student": {**student_label(row), "campus": row.get("campus"),
                    "batch": row.get("batch"), "trimester": row.get("trimester")},
        "summary": {
            "subjects": row.get("subjects_count"),
            "subjects_at_or_above_average": row.get("above_average_count"),
            "strongest_subject": row.get("strongest_subject"),
            "attendance_at_or_above_average_subjects": att.get("at_or_above_count"),
        },
        "narrative": {
            "headline": pp.get("headline"),
            "personal_pattern": pp.get("observation"),
            "attendance": " ".join(x for x in (ast.get("headline"), ast.get("observation")) if x),
            "next_moves": actions,
        },
        "subjects": [{"subject": s.get("name"), "your_marks": s.get("student_score"),
                      "class_average": s.get("class_marks_average"),
                      "delta": s.get("score_delta"), "attendance": s.get("student_attendance")}
                     for s in subjects],
        "accuracy": ({"overall_pct": acc.get("overall_pct"), "label": acc.get("overall_label"),
                      "agent_accuracy": acc.get("agent_accuracy"),
                      "panel_verdict": acc.get("panel_verdict")} if acc else
                     {"note": "not yet audited"}),
        "report_link_base": settings.report_public_base_url,
    }


def _status_impl(svc, p: StatusParams) -> dict:
    if svc.campus_scope(p.campus) == []:
        return not_found("scope")
    run_id = svc.latest_run(p.campus, p.batch)
    if not run_id:
        return {"available": False, "note": f"no completed final run for {p.campus}/{p.batch}"}
    jobs = (svc.client.table("student_report_jobs").select("status,student_id")
            .eq("run_id", run_id).limit(100000).execute()).data or []
    from collections import Counter
    c = Counter(j["status"] for j in jobs)
    held = [j["student_id"] for j in jobs if j["status"] == "manual_review"][:50]
    total = len(jobs) or 1
    return {"campus": p.campus, "batch": p.batch, "trimester": str(p.trimester),
            "total": len(jobs), "ready": c.get("ready_to_send", 0),
            "held_for_review": c.get("manual_review", 0), "failed": c.get("failed", 0),
            "ready_pct": round(100 * c.get("ready_to_send", 0) / total, 1),
            "held_students_sample": held}


def register(mcp, get_service):
    # NOTE: this module is the SECONDARY report layer. Raw student data lives in tools/students.py
    # (list_students / get_student / student_marks / student_attendance) — prefer those. Here we
    # only expose the generated narrative report and the pipeline status.

    @mcp.tool(title="Get Student Report", annotations=READONLY_ANNOTATIONS)
    async def get_student_report(params: ReportParams) -> dict:
        """
        WHAT: One student's full validated report — headline, personal pattern, attendance note,
        the three next moves, per-subject marks vs class, and the report's accuracy score.
        USE WHEN they say: 'open <id>'s report', 'how did <name> do', 'show me JJ24PG099',
        'what are this student's next steps'.
        DO NOT USE WHEN searching many students (use search_students) or for cohort stats.
        RETURNS: student, summary, narrative, subjects, accuracy. found:false if out of scope.
        """
        return _report_impl(await get_service(), params)

    @mcp.tool(title="Report Pipeline Status", annotations=READONLY_ANNOTATIONS)
    async def report_pipeline_status(params: StatusParams) -> dict:
        """
        WHAT: How many reports are ready / held for review / failed for a scope, with a sample of
        held students.
        USE WHEN they say: 'are the reports ready', 'how many are pending', 'what's held for
        review in indore', 'pipeline status'.
        DO NOT USE WHEN they want accuracy (use accuracy_overview) or a specific report.
        RETURNS: ready / held_for_review / failed counts + ready_pct. available:false if no run.
        """
        return _status_impl(await get_service(), params)
