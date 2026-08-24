"""Early-warning triage for faculty — at-risk students, attendance watch, zero alerts.
Derived from the deterministic figures in each report's evidence_packet. Read-only."""
from pydantic import BaseModel, Field

from annotations import READONLY_ANNOTATIONS
from guardrails import clamp_limit
from tools.common import report_rows, student_label

_COLS = "student_id,full_name,first_name,campus,batch,trimester,above_average_count,subjects_count,evidence_packet"


class RiskParams(BaseModel):
    campus: str | None = None
    batch: str | None = None
    trimester: str | None = None
    limit: int = Field(default=25, description="max students, 1-50")


class AttParams(BaseModel):
    campus: str | None = None
    batch: str | None = None
    trimester: str | None = None
    threshold: float = Field(default=75.0, description="attendance % below which to flag (default 75)")
    limit: int = Field(default=25, description="max students, 1-50")


def _subjects(row):
    return (row.get("evidence_packet") or {}).get("subjects", []) or []


def _components(row):
    return (row.get("evidence_packet") or {}).get("components", []) or []


def _risk_impl(svc, p: RiskParams) -> dict:
    rows = report_rows(svc, campus=p.campus, batch=p.batch, trimester=p.trimester, cols=_COLS)
    out = []
    for r in rows:
        subs = _subjects(r)
        zeros = [c.get("name") for c in _components(r)
                 if isinstance(c.get("student_score"), (int, float)) and c.get("student_score") == 0]
        low_att = [s.get("name") for s in subs
                   if isinstance(s.get("student_attendance"), (int, float))
                   and s.get("student_attendance") < 75]
        below_avg = [s.get("name") for s in subs
                     if isinstance(s.get("score_delta"), (int, float)) and s.get("score_delta") < 0]
        aa = r.get("above_average_count") or 0
        n = r.get("subjects_count") or len(subs) or 1
        # composite risk: zeros weigh most, then low attendance, then majority-below-average
        score = len(zeros) * 3 + len(low_att) * 2 + (2 if aa <= max(1, n // 4) else 0)
        if score == 0:
            continue
        out.append({**student_label(r), "campus": r.get("campus"), "risk_score": score,
                    "recorded_zeros": zeros[:5], "low_attendance_subjects": low_att[:5],
                    "subjects_below_class_average": len(below_avg), "subjects_total": n})
    out.sort(key=lambda x: x["risk_score"], reverse=True)
    out = out[:clamp_limit(p.limit)]
    return {"at_risk_count": len(out), "students": out,
            "note": "Ranked by recorded zeros, low attendance, and below-average breadth."}


def _attendance_impl(svc, p: AttParams) -> dict:
    rows = report_rows(svc, campus=p.campus, batch=p.batch, trimester=p.trimester, cols=_COLS)
    out = []
    for r in rows:
        low = [{"subject": s.get("name"), "attendance": s.get("student_attendance"),
                "class_average": s.get("class_attendance_average")}
               for s in _subjects(r)
               if isinstance(s.get("student_attendance"), (int, float))
               and s.get("student_attendance") < p.threshold]
        if low:
            worst = min(s["attendance"] for s in low)
            out.append({**student_label(r), "campus": r.get("campus"),
                        "lowest_attendance": worst, "subjects_below_threshold": low[:6]})
    out.sort(key=lambda x: x["lowest_attendance"])
    out = out[:clamp_limit(p.limit)]
    return {"threshold": p.threshold, "count": len(out), "students": out}


def _zero_impl(svc, p: RiskParams) -> dict:
    rows = report_rows(svc, campus=p.campus, batch=p.batch, trimester=p.trimester, cols=_COLS)
    out = []
    for r in rows:
        zeros = [c.get("name") for c in _components(r)
                 if isinstance(c.get("student_score"), (int, float)) and c.get("student_score") == 0]
        if zeros:
            out.append({**student_label(r), "campus": r.get("campus"),
                        "zero_components": zeros[:8], "zero_count": len(zeros)})
    out.sort(key=lambda x: x["zero_count"], reverse=True)
    out = out[:clamp_limit(p.limit)]
    return {"students_with_zeros": len(out), "students": out,
            "note": "A recorded zero is the most urgent signal — each needs a recovery action."}


def register(mcp, get_service):
    @mcp.tool(title="At-Risk Students", annotations=READONLY_ANNOTATIONS)
    async def at_risk_students(params: RiskParams) -> dict:
        """
        WHAT: The students most at risk this trimester, ranked by a composite of recorded zeros,
        low attendance, and how many subjects sit below the class average.
        USE WHEN they say: 'who is struggling', 'who needs attention', 'at-risk list', 'who should
        we intervene with', 'weakest students in indore T5'.
        DO NOT USE WHEN they want only attendance (attendance_watch) or only zeros (zero_alerts).
        RETURNS: at_risk_count + ranked students with their risk signals.
        """
        return _risk_impl(await get_service(), params)

    @mcp.tool(title="Attendance Watch", annotations=READONLY_ANNOTATIONS)
    async def attendance_watch(params: AttParams) -> dict:
        """
        WHAT: Students with any subject attendance below a threshold (default 75%), worst first.
        USE WHEN they say: 'who has low attendance', 'attendance below 70', 'attendance watchlist',
        'who is missing classes'.
        DO NOT USE WHEN they want the composite risk list (at_risk_students).
        RETURNS: threshold + students with their below-threshold subjects.
        """
        return _attendance_impl(await get_service(), params)

    @mcp.tool(title="Zero Alerts", annotations=READONLY_ANNOTATIONS)
    async def zero_alerts(params: RiskParams) -> dict:
        """
        WHAT: Students with a recorded ZERO in any graded component — the most urgent intervention
        signal.
        USE WHEN they say: 'who has zeros', 'missing submissions', 'who scored 0', 'urgent cases'.
        DO NOT USE WHEN they want general weakness (at_risk_students) or attendance.
        RETURNS: students_with_zeros + the components each zeroed.
        """
        return _zero_impl(await get_service(), params)
