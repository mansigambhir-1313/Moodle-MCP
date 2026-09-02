"""Report retrieval — find students, open one full report, check data availability. Read-only."""
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


class AvailabilityParams(BaseModel):
    campus: str | None = Field(default=None, description="one campus (within your grant); omit for all", max_length=64)
    batch: str | None = Field(default=None, description="batch e.g. '2024-26'; omit for all", max_length=64)


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


def _availability_impl(svc, p: AvailabilityParams) -> dict:
    """What report data exists per (campus, batch) in the caller's grant: latest
    snapshot time, roster size, and the trimesters covered by that snapshot."""
    if p.campus and svc.campus_scope(p.campus) == []:
        return not_found("scope")
    q = (svc.client.table("extraction_runs")
         .select("campus,batch,run_id,finished_at")
         .eq("status", "completed").eq("purpose", settings.report_purpose)
         .not_.is_("finished_at", "null")
         .order("finished_at", desc=True).limit(200))
    q = svc.apply_campus(q, requested=p.campus)
    if p.batch:
        q = q.eq("batch", p.batch)
    runs = q.execute().data or []
    latest, order = {}, []
    for r in runs:  # newest-first: first hit per scope is the live snapshot
        k = (r["campus"], r["batch"])
        if k not in latest:
            latest[k] = r
            order.append(k)
    scopes = []
    for k in order[:12]:
        r = latest[k]
        n = (svc.client.table("students").select("student_id", count="exact")
             .eq("campus", r["campus"]).eq("batch", r["batch"])
             .limit(1).execute()).count or 0
        from tools.common import courses_for
        tris = sorted({c["trimester"] for c in courses_for(svc, r["run_id"]).values()
                       if c["trimester"]}, key=str)
        scopes.append({"campus": r["campus"], "batch": r["batch"],
                       "latest_snapshot": r["finished_at"], "students": n,
                       "trimesters_with_data": tris})
    return {"scopes": scopes, "found": bool(scopes),
            "note": ("Reports are generated on demand with create_report and read "
                     "with get_student_report — no pre-built queue to wait on.")}


def register(mcp, get_service):
    # NOTE: this module is the SECONDARY report layer. Raw student data lives in tools/students.py
    # (list_students / get_student / student_marks / student_attendance) — prefer those. Here we
    # only expose the generated narrative report and the data-availability overview.

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

    @mcp.tool(title="Report Data Availability", annotations=READONLY_ANNOTATIONS)
    async def report_data_availability(params: AvailabilityParams) -> dict:
        """
        WHAT: What report data exists in your grant — per campus/batch: when the latest Moodle
        snapshot finished, how many students are on the roster, and which trimesters have data.
        USE WHEN they say: 'is the data in', 'which trimesters do we have', 'how fresh is the
        data', 'can I generate reports for noida yet', 'what batches are covered'.
        DO NOT USE WHEN they want one student's report (use get_student_report) or want to
        generate a shareable link (use create_report).
        RETURNS: scopes[{campus, batch, latest_snapshot, students, trimesters_with_data}].
        """
        return _availability_impl(await get_service(), params)
