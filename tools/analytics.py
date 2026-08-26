"""Cohort analytics from the RAW marks + attendance — marks overview, attendance overview, top
performers, campus comparison. Read-only, campus-scoped."""
from pydantic import BaseModel, Field

from annotations import READONLY_ANNOTATIONS
from guardrails import clamp_limit
from tools.common import cohort_rollup, courses_for


class ScopeParams(BaseModel):
    campus: str = Field(description="campus (within your grant)", max_length=64)
    batch: str = Field(description="batch e.g. '2024-26'", max_length=64)
    trimester: str | None = Field(default=None, description="restrict to one trimester", max_length=8)


class TopParams(ScopeParams):
    limit: int = Field(default=10, description="how many students, 1-50", ge=1, le=50)


class CompareParams(BaseModel):
    batch: str = Field(description="batch e.g. '2024-26'", max_length=64)
    trimester: str | None = Field(default=None, description="restrict to one trimester", max_length=8)


def _mean(xs, nd=1):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), nd) if xs else None


def _rollup(svc, campus, batch, trimester):
    if svc.campus_scope(campus) == []:
        return None
    run_id = svc.latest_run(campus, batch)
    if not run_id:
        return None
    return cohort_rollup(svc, run_id, courses_for(svc, run_id, trimester))


def _marks_impl(svc, p: ScopeParams) -> dict:
    roll = _rollup(svc, p.campus, p.batch, p.trimester)
    if not roll:
        return {"available": False, "note": "no completed run for this scope"}
    marks = [r["mark_pct"] for r in roll.values() if r["mark_pct"] is not None]
    zeros = sum(1 for r in roll.values() if r["zeros"])
    return {"campus": p.campus, "batch": p.batch, "trimester": p.trimester or "all",
            "students_graded": len(marks), "mean_mark_pct": _mean(marks),
            "pass_rate_pct": (round(100 * sum(1 for x in marks if x >= 40) / len(marks), 1)
                              if marks else None),
            "students_with_zeros": zeros,
            "distribution": {"below_40": sum(1 for x in marks if x < 40),
                             "40_to_60": sum(1 for x in marks if 40 <= x < 60),
                             "60_to_75": sum(1 for x in marks if 60 <= x < 75),
                             "75_plus": sum(1 for x in marks if x >= 75)}}


def _attendance_impl(svc, p: ScopeParams) -> dict:
    roll = _rollup(svc, p.campus, p.batch, p.trimester)
    if not roll:
        return {"available": False, "note": "no completed run for this scope"}
    att = [r["attendance_pct"] for r in roll.values() if r["attendance_pct"] is not None]
    return {"campus": p.campus, "batch": p.batch, "trimester": p.trimester or "all",
            "students": len(att), "mean_attendance_pct": _mean(att),
            "below_75_pct_count": sum(1 for x in att if x < 75),
            "below_65_pct_count": sum(1 for x in att if x < 65)}


def _top_impl(svc, p: TopParams) -> dict:
    roll = _rollup(svc, p.campus, p.batch, p.trimester)
    if not roll:
        return {"available": False, "note": "no completed run for this scope"}
    ranked = sorted((r | {"student_id": sid} for sid, r in roll.items()
                     if r["mark_pct"] is not None),
                    key=lambda r: r["mark_pct"], reverse=True)[:clamp_limit(p.limit)]
    return {"count": len(ranked),
            "students": [{"student_id": r["student_id"], "name": r["name"],
                          "overall_mark_pct": r["mark_pct"],
                          "attendance_pct": r["attendance_pct"]} for r in ranked]}


def _compare_impl(svc, p: CompareParams) -> dict:
    scope = svc.campus_scope(None)
    campuses = scope if scope is not None else ["indore", "lucknow", "noida", "jaipur"]
    out = []
    for c in campuses:
        roll = _rollup(svc, c, p.batch, p.trimester)
        if not roll:
            continue
        marks = [r["mark_pct"] for r in roll.values() if r["mark_pct"] is not None]
        att = [r["attendance_pct"] for r in roll.values() if r["attendance_pct"] is not None]
        out.append({"campus": c, "students": len(roll), "mean_mark_pct": _mean(marks),
                    "mean_attendance_pct": _mean(att)})
    if not out:
        return {"available": False, "note": "no completed runs for this batch in your scope"}
    out.sort(key=lambda x: (x["mean_mark_pct"] is None, -(x["mean_mark_pct"] or 0)))
    return {"batch": p.batch, "trimester": p.trimester or "all", "campuses": out}


def register(mcp, get_service):
    @mcp.tool(title="Marks Overview", annotations=READONLY_ANNOTATIONS)
    async def marks_overview(params: ScopeParams) -> dict:
        """
        WHAT: Cohort marks snapshot from the raw gradebook — mean mark %, pass rate, a mark
        distribution, and how many students have a recorded zero.
        USE WHEN they say: 'how did the batch do', 'overall marks', 'pass rate for indore 2024-26',
        'mark distribution', 'class performance'.
        DO NOT USE WHEN they want attendance (attendance_overview) or one subject/student.
        RETURNS: mean_mark_pct, pass_rate_pct, distribution, students_with_zeros.
        """
        return _marks_impl(await get_service(), params)

    @mcp.tool(title="Attendance Overview", annotations=READONLY_ANNOTATIONS)
    async def attendance_overview(params: ScopeParams) -> dict:
        """
        WHAT: Cohort attendance snapshot from the raw records — mean attendance % and how many
        students fall below 75% / 65%.
        USE WHEN they say: 'overall attendance', 'how is attendance in this batch', 'how many below
        75%', 'attendance summary'.
        DO NOT USE WHEN they want marks (marks_overview) or one student.
        RETURNS: mean_attendance_pct, below_75_pct_count, below_65_pct_count.
        """
        return _attendance_impl(await get_service(), params)

    @mcp.tool(title="Top Performers", annotations=READONLY_ANNOTATIONS)
    async def top_performers(params: TopParams) -> dict:
        """
        WHAT: The highest-scoring students in a scope by overall graded-mark %, from the raw data.
        USE WHEN they say: 'top students', 'who is doing best', 'highest marks', 'leaderboard',
        'who to recognise'.
        DO NOT USE WHEN they want at-risk students (at_risk_students) or one student.
        RETURNS: ranked students with overall_mark_pct and attendance_pct.
        """
        return _top_impl(await get_service(), params)

    @mcp.tool(title="Cohort Compare", annotations=READONLY_ANNOTATIONS)
    async def cohort_compare(params: CompareParams) -> dict:
        """
        WHAT: Side-by-side comparison of your campuses for a batch — student count, mean mark %,
        mean attendance % — from the raw data.
        USE WHEN they say: 'compare campuses', 'which campus is best', 'indore vs lucknow',
        'campus comparison'.
        DO NOT USE WHEN scoped to one campus (use marks_overview / attendance_overview).
        RETURNS: campuses[] each with mean_mark_pct and mean_attendance_pct.
        """
        return _compare_impl(await get_service(), params)
