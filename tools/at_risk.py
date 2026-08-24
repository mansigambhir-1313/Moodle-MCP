"""Early-warning triage from the RAW marks + attendance (covers every ingested student, not only
those with a report). At-risk ranking, attendance watch, zero alerts. Read-only, campus-scoped."""
from pydantic import BaseModel, Field

from annotations import READONLY_ANNOTATIONS
from guardrails import clamp_limit, not_found
from tools.common import cohort_rollup, courses_for


class RiskParams(BaseModel):
    campus: str = Field(description="campus (within your grant)")
    batch: str = Field(description="batch e.g. '2024-26'")
    trimester: str | None = Field(default=None, description="restrict to one trimester")
    limit: int = Field(default=25, description="max students, 1-50")


class AttParams(RiskParams):
    threshold: float = Field(default=75.0, description="attendance % below which to flag (default 75)")


def _rollup(svc, p):
    if svc.campus_scope(p.campus) == []:
        return None
    run_id = svc.latest_run(p.campus, p.batch)
    if not run_id:
        return None
    return cohort_rollup(svc, run_id, courses_for(svc, run_id, p.trimester))


def _risk_impl(svc, p: RiskParams) -> dict:
    roll = _rollup(svc, p)
    if roll is None:
        return {"available": False, "note": "no completed run for this scope"}
    out = []
    for sid, r in roll.items():
        zeros, low_att = r["zeros"], r["low_attendance_subjects"]
        low_mark = r["mark_pct"] is not None and r["mark_pct"] < 40
        score = len(zeros) * 3 + len(low_att) * 2 + (2 if low_mark else 0)
        if score == 0:
            continue
        out.append({"student_id": sid, "name": r["name"], "risk_score": score,
                    "recorded_zeros": zeros, "overall_mark_pct": r["mark_pct"],
                    "overall_attendance_pct": r["attendance_pct"],
                    "low_attendance_subjects": [s for s, _ in low_att][:5]})
    out.sort(key=lambda x: x["risk_score"], reverse=True)
    return {"at_risk_count": len(out), "students": out[:clamp_limit(p.limit)],
            "note": "Ranked by recorded zeros, low attendance, and sub-40% overall marks."}


def _attendance_impl(svc, p: AttParams) -> dict:
    roll = _rollup(svc, p)
    if roll is None:
        return {"available": False, "note": "no completed run for this scope"}
    out = []
    for sid, r in roll.items():
        if r["attendance_pct"] is not None and r["attendance_pct"] < p.threshold:
            out.append({"student_id": sid, "name": r["name"],
                        "overall_attendance_pct": r["attendance_pct"],
                        "low_subjects": [{"subject": s, "pct": a} for s, a in r["low_attendance_subjects"]][:6]})
    out.sort(key=lambda x: x["overall_attendance_pct"])
    return {"threshold": p.threshold, "count": len(out), "students": out[:clamp_limit(p.limit)]}


def _zero_impl(svc, p: RiskParams) -> dict:
    roll = _rollup(svc, p)
    if roll is None:
        return {"available": False, "note": "no completed run for this scope"}
    out = [{"student_id": sid, "name": r["name"], "zero_components": r["zeros"],
            "zero_count": len(r["zeros"])} for sid, r in roll.items() if r["zeros"]]
    out.sort(key=lambda x: x["zero_count"], reverse=True)
    return {"students_with_zeros": len(out), "students": out[:clamp_limit(p.limit)],
            "note": "A recorded zero is the most urgent signal — each needs a recovery action."}


def register(mcp, get_service):
    @mcp.tool(title="At-Risk Students", annotations=READONLY_ANNOTATIONS)
    async def at_risk_students(params: RiskParams) -> dict:
        """
        WHAT: Students most at risk, from the raw gradebook + attendance — ranked by recorded zeros,
        low attendance, and sub-40% overall marks. Covers every student, report or not.
        USE WHEN they say: 'who is struggling', 'at-risk list', 'who needs intervention', 'weakest
        students in indore 2024-26'.
        DO NOT USE WHEN they want only attendance (attendance_watch) or only zeros (zero_alerts).
        RETURNS: at_risk_count + ranked students with their signals.
        """
        return _risk_impl(await get_service(), params)

    @mcp.tool(title="Attendance Watch", annotations=READONLY_ANNOTATIONS)
    async def attendance_watch(params: AttParams) -> dict:
        """
        WHAT: Students whose overall attendance is below a threshold (default 75%), worst first, with
        their low subjects — from the raw attendance records.
        USE WHEN they say: 'who has low attendance', 'attendance below 70', 'attendance watchlist',
        'who is missing classes'.
        DO NOT USE WHEN they want the composite risk list (at_risk_students).
        RETURNS: threshold + students with overall_attendance_pct and low subjects.
        """
        return _attendance_impl(await get_service(), params)

    @mcp.tool(title="Zero Alerts", annotations=READONLY_ANNOTATIONS)
    async def zero_alerts(params: RiskParams) -> dict:
        """
        WHAT: Students with a recorded ZERO in any graded component — the most urgent signal — from
        the raw gradebook.
        USE WHEN they say: 'who has zeros', 'missing submissions', 'who scored 0', 'urgent cases'.
        DO NOT USE WHEN they want general weakness (at_risk_students) or attendance.
        RETURNS: students_with_zeros + the components each zeroed.
        """
        return _zero_impl(await get_service(), params)
