"""Innovative, higher-order tools — longitudinal trajectory, single-pane student & cohort views,
and the auto intervention watchlist. All read-only, campus-scoped, built on the raw-data helpers.
See docs/INNOVATION_ROADMAP.md."""
from collections import defaultdict

from pydantic import BaseModel, Field

from annotations import READONLY_ANNOTATIONS
from guardrails import clamp_limit, enrolled_no_data, not_found
from tools.common import (accuracy_rows, attendance_for, attendance_pct, cohort_rollup,
                          courses_for, find_student, marks_for, pct, scope_marks)


class StudentParams(BaseModel):
    student_id: str = Field(description="enrolment id, e.g. 'JJ24PG099'", max_length=64)


class ScopeParams(BaseModel):
    campus: str = Field(description="campus (within your grant)", max_length=64)
    batch: str = Field(description="batch e.g. '2024-26'", max_length=64)
    trimester: str | None = Field(default=None, description="latest trimester if omitted", max_length=8)
    limit: int = Field(default=25, description="max students where applicable, 1-50", ge=1, le=50)


# --- per-trimester series for one student ------------------------------------
def _series(svc, run_id, student_id, all_courses):
    tri_of = {cid: m["trimester"] for cid, m in all_courses.items()}
    marks = marks_for(svc, run_id, student_id=student_id)
    att = attendance_for(svc, run_id, student_id=student_id)
    magg = defaultdict(lambda: [0.0, 0.0])
    for m in marks:
        t = tri_of.get(m["course_id"])
        if t and m.get("graded") and isinstance(m.get("obtained_score"), (int, float)) \
                and isinstance(m.get("max_score"), (int, float)):
            magg[t][0] += m["obtained_score"]; magg[t][1] += m["max_score"]
    aagg = defaultdict(list)
    for a in att:
        t = tri_of.get(a["course_id"])
        if t:
            aagg[t].append(a.get("status_norm"))
    series = []
    for t in sorted(set(list(magg) + list(aagg))):
        mk = pct(magg[t][0], magg[t][1]) if t in magg else None
        _, _, ap = attendance_pct([{"status_norm": s} for s in aagg.get(t, [])])
        series.append({"trimester": t, "mark_pct": mk, "attendance_pct": ap})
    return series


def _trend(series):
    graded = [s for s in series if s["mark_pct"] is not None]
    if len(graded) < 2:
        return {"label": "insufficient_history", "delta": None}
    delta = round(graded[-1]["mark_pct"] - graded[-2]["mark_pct"], 1)
    label = "improving" if delta >= 3 else "declining" if delta <= -3 else "stable"
    return {"label": label, "delta": delta,
            "from_trimester": graded[-2]["trimester"], "to_trimester": graded[-1]["trimester"]}


def _traj_impl(svc, p: StudentParams) -> dict:
    stu = find_student(svc, p.student_id)
    if not stu:
        return not_found("student")
    run_id = svc.latest_run(stu["campus"], stu["batch"])
    if not run_id:
        return enrolled_no_data(stu)
    series = _series(svc, run_id, p.student_id, courses_for(svc, run_id))
    return {"found": True, "student_id": p.student_id, "name": stu.get("student_name"),
            "campus": stu.get("campus"), "batch": stu.get("batch"),
            "trajectory": series, "trend": _trend(series)}


def _rank(value, population):
    pop = [x for x in population if isinstance(x, (int, float))]
    if value is None or not pop:
        return None
    at_or_below = sum(1 for x in pop if x <= value)
    return round(100 * at_or_below / len(pop))


def _360_impl(svc, p: StudentParams) -> dict:
    stu = find_student(svc, p.student_id)
    if not stu:
        return not_found("student")
    campus, batch = stu["campus"], stu["batch"]
    run_id = svc.latest_run(campus, batch)
    if not run_id:
        return enrolled_no_data(stu)
    all_courses = courses_for(svc, run_id)
    series = _series(svc, run_id, p.student_id, all_courses)
    trend = _trend(series)
    latest = next((s for s in reversed(series) if s["mark_pct"] is not None), None)
    latest_tri = latest["trimester"] if latest else None
    roll = cohort_rollup(svc, run_id, courses_for(svc, run_id, latest_tri)) if latest_tri else {}
    me = roll.get(p.student_id, {})
    marks_pop = [r["mark_pct"] for r in roll.values()]
    att_pop = [r["attendance_pct"] for r in roll.values()]
    flags = []
    if me.get("zeros"):
        flags.append(f"{len(me['zeros'])} recorded zero(s)")
    if me.get("attendance_pct") is not None and me["attendance_pct"] < 75:
        flags.append(f"attendance {me['attendance_pct']}% (below 75%)")
    if trend["label"] == "declining":
        flags.append(f"marks declining ({trend['delta']} pts)")
    acc = accuracy_rows(svc, campus=campus, batch=batch,
                        cols="student_id,overall_pct,overall_label")
    acc = next((a for a in acc if a.get("student_id") == p.student_id), None)
    return {"found": True,
            "student": {"student_id": p.student_id, "name": stu.get("student_name"),
                        "campus": campus, "batch": batch, "section": stu.get("section_group")},
            "latest_trimester": latest_tri,
            "latest_mark_pct": me.get("mark_pct"), "latest_attendance_pct": me.get("attendance_pct"),
            "mark_percentile": _rank(me.get("mark_pct"), marks_pop),
            "attendance_percentile": _rank(me.get("attendance_pct"), att_pop),
            "trend": trend, "trajectory": series,
            "risk_flags": flags or ["none"],
            "recorded_zeros": me.get("zeros", []),
            "report_accuracy": ({"overall_pct": acc.get("overall_pct"),
                                 "label": acc.get("overall_label")} if acc else None)}


def _pulse_impl(svc, p: ScopeParams) -> dict:
    if svc.campus_scope(p.campus) == []:
        return not_found("scope")
    run_id = svc.latest_run(p.campus, p.batch)
    if not run_id:
        return {"available": False, "note": "no completed run"}
    roll = cohort_rollup(svc, run_id, courses_for(svc, run_id, p.trimester))
    marks = [r["mark_pct"] for r in roll.values() if r["mark_pct"] is not None]
    att = [r["attendance_pct"] for r in roll.values() if r["attendance_pct"] is not None]
    zeros = sum(1 for r in roll.values() if r["zeros"])
    at_risk = sum(1 for r in roll.values()
                  if r["zeros"] or (r["mark_pct"] is not None and r["mark_pct"] < 40)
                  or (r["attendance_pct"] is not None and r["attendance_pct"] < 65))
    m = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731
    return {"campus": p.campus, "batch": p.batch, "trimester": p.trimester or "latest",
            "cohort_size": len(roll),
            "mean_mark_pct": m(marks),
            "pass_rate_pct": (round(100 * sum(1 for x in marks if x >= 40) / len(marks), 1)
                              if marks else None),
            "mean_attendance_pct": m(att),
            "students_with_zeros": zeros, "at_risk_count": at_risk,
            "mark_distribution": {"below_40": sum(1 for x in marks if x < 40),
                                  "40_to_60": sum(1 for x in marks if 40 <= x < 60),
                                  "60_to_75": sum(1 for x in marks if 60 <= x < 75),
                                  "75_plus": sum(1 for x in marks if x >= 75)},
            "attendance_below_75": sum(1 for x in att if x < 75)}


def _action(r):
    if r["zeros"]:
        return "Chase missing submissions — recover the recorded zero(s)."
    if r["attendance_pct"] is not None and r["attendance_pct"] < 65:
        return "Attendance intervention — below the eligibility comfort zone."
    if r["mark_pct"] is not None and r["mark_pct"] < 40:
        return "Academic support — overall marks below the pass line."
    return "Monitor — early-warning signals present."


def _watchlist_impl(svc, p: ScopeParams) -> dict:
    if svc.campus_scope(p.campus) == []:
        return not_found("scope")
    run_id = svc.latest_run(p.campus, p.batch)
    if not run_id:
        return {"available": False, "note": "no completed run"}
    roll = cohort_rollup(svc, run_id, courses_for(svc, run_id, p.trimester))
    out = []
    for sid, r in roll.items():
        reasons = []
        if r["zeros"]:
            reasons.append(f"{len(r['zeros'])} zero(s)")
        if r["attendance_pct"] is not None and r["attendance_pct"] < 75:
            reasons.append(f"attendance {r['attendance_pct']}%")
        if r["mark_pct"] is not None and r["mark_pct"] < 40:
            reasons.append(f"marks {r['mark_pct']}%")
        if not reasons:
            continue
        score = len(r["zeros"]) * 3 + (2 if (r["attendance_pct"] or 100) < 75 else 0) \
            + (2 if (r["mark_pct"] or 100) < 40 else 0)
        out.append({"student_id": sid, "name": r["name"], "priority": score,
                    "reasons": reasons, "suggested_action": _action(r),
                    "mark_pct": r["mark_pct"], "attendance_pct": r["attendance_pct"]})
    out.sort(key=lambda x: x["priority"], reverse=True)
    return {"campus": p.campus, "batch": p.batch, "watchlist_size": len(out),
            "students": out[:clamp_limit(p.limit)]}


def _declining_impl(svc, p: ScopeParams) -> dict:
    if svc.campus_scope(p.campus) == []:
        return not_found("scope")
    run_id = svc.latest_run(p.campus, p.batch)
    if not run_id:
        return {"available": False, "note": "no completed run"}
    all_courses = courses_for(svc, run_id)  # all trimesters — trajectory needs history
    tri_of = {cid: m["trimester"] for cid, m in all_courses.items()}
    names = {r["student_id"]: r.get("student_name") for r in
             (svc.client.table("students").select("student_id,student_name")
              .eq("run_id", run_id).limit(100000).execute()).data or []}
    marks = scope_marks(svc, run_id, list(all_courses))
    agg = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))  # student -> tri -> [obt,max]
    for m in marks:
        t = tri_of.get(m["course_id"])
        if t and m.get("graded") and isinstance(m.get("obtained_score"), (int, float)) \
                and isinstance(m.get("max_score"), (int, float)):
            a = agg[m["student_id"]][t]
            a[0] += m["obtained_score"]; a[1] += m["max_score"]
    out = []
    for sid, tris in agg.items():
        series = [(t, pct(o, mx)) for t, (o, mx) in sorted(tris.items()) if mx]
        if len(series) < 2:
            continue
        delta = round(series[-1][1] - series[-2][1], 1)
        if delta >= -3:  # only genuine declines
            continue
        out.append({"student_id": sid, "name": names.get(sid, sid), "drop_pts": delta,
                    "from_trimester": series[-2][0], "to_trimester": series[-1][0],
                    "previous_mark_pct": series[-2][1], "latest_mark_pct": series[-1][1]})
    out.sort(key=lambda x: x["drop_pts"])
    from guardrails import clamp_limit as _cl
    return {"campus": p.campus, "batch": p.batch, "declining_count": len(out),
            "students": out[:_cl(p.limit)],
            "note": "Students whose latest-trimester marks fell 3+ points vs the prior term."}


def register(mcp, get_service):
    @mcp.tool(title="Declining Students", annotations=READONLY_ANNOTATIONS)
    async def declining_students(params: ScopeParams) -> dict:
        """
        WHAT: Students whose marks DROPPED most from the previous trimester to the latest — the
        cohort-wide early-warning list that a snapshot can never show.
        USE WHEN they say: 'who is slipping', 'whose marks dropped', 'who got worse this term',
        'declining students', 'biggest drops'.
        DO NOT USE WHEN they want one student's trend (student_trajectory) or the at-risk list.
        RETURNS: declining students ranked by drop_pts, with from/to trimester + marks.
        """
        return _declining_impl(await get_service(), params)

    @mcp.tool(title="Student Trajectory", annotations=READONLY_ANNOTATIONS)
    async def student_trajectory(params: StudentParams) -> dict:
        """
        WHAT: A student's marks and attendance TREND across trimesters, with an improving /
        declining / stable label — the longitudinal view, not a single snapshot.
        USE WHEN they say: 'is <name> improving', 'how has <id> changed over terms', 'trajectory',
        'are they getting better or worse', 'trend for this student'.
        DO NOT USE WHEN they want the current record only (get_student).
        RETURNS: trajectory[] per trimester + a trend label and delta.
        """
        return _traj_impl(await get_service(), params)

    @mcp.tool(title="Student 360", annotations=READONLY_ANNOTATIONS)
    async def student_360(params: StudentParams) -> dict:
        """
        WHAT: One-call complete view of a student — latest marks/attendance, cohort percentile rank,
        trajectory + trend, risk flags, recorded zeros, and report-accuracy. The dashboard's
        student drawer.
        USE WHEN they say: 'give me everything on <id>', 'full picture of <name>', 'student profile',
        'how is <id> doing overall and vs the class'.
        DO NOT USE WHEN they want only marks/attendance/trajectory (use the focused tools).
        RETURNS: latest stats, percentiles, trend, trajectory, risk_flags, report_accuracy.
        """
        return _360_impl(await get_service(), params)

    @mcp.tool(title="Cohort Pulse", annotations=READONLY_ANNOTATIONS)
    async def cohort_pulse(params: ScopeParams) -> dict:
        """
        WHAT: One-call cohort dashboard — size, mean mark %, pass rate, mean attendance, at-risk
        count, students-with-zeros, and the mark distribution. The landing-screen KPIs.
        USE WHEN they say: 'how is indore 2024-26 doing', 'give me the headline numbers', 'cohort
        dashboard', 'summary of the batch', 'pulse'.
        DO NOT USE WHEN they want the at-risk list (watchlist) or per-subject detail.
        RETURNS: the KPI bundle for the scope.
        """
        return _pulse_impl(await get_service(), params)

    @mcp.tool(title="Watchlist", annotations=READONLY_ANNOTATIONS)
    async def watchlist(params: ScopeParams) -> dict:
        """
        WHAT: The auto-generated intervention watchlist — students flagged by zeros, low attendance,
        or failing marks, ranked by priority, each with the REASON and a SUGGESTED ACTION.
        USE WHEN they say: 'who needs intervention', 'action list', 'who should we call in', 'build
        my watchlist', 'priority students to help'.
        DO NOT USE WHEN they want cohort KPIs (cohort_pulse) or one student (student_360).
        RETURNS: ranked students with reasons + suggested_action.
        """
        return _watchlist_impl(await get_service(), params)
