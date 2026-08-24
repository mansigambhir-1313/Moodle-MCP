"""Cohort analytics — campus overview, per-subject performance, campus comparison.
Derived from the deterministic figures in each report's evidence_packet + the accuracy scores.
Read-only, campus-scoped."""
from collections import Counter, defaultdict

from pydantic import BaseModel, Field

from annotations import READONLY_ANNOTATIONS
from guardrails import not_found
from tools.common import accuracy_rows, report_rows

_COLS = "student_id,campus,batch,trimester,above_average_count,subjects_count,strongest_subject,personal_pattern_kind,evidence_packet"


class ScopeParams(BaseModel):
    campus: str | None = None
    batch: str | None = None
    trimester: str | None = None


class SubjectParams(ScopeParams):
    subject: str | None = Field(default=None, description="optional subject-name fragment filter")


class CompareParams(BaseModel):
    batch: str = Field(description="batch e.g. '2024-26'")
    trimester: str = Field(description="trimester number")


def _mean(xs, nd=1):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), nd) if xs else None


def _subjects(row):
    return (row.get("evidence_packet") or {}).get("subjects", []) or []


def _overview_impl(svc, p: ScopeParams) -> dict:
    rows = report_rows(svc, campus=p.campus, batch=p.batch, trimester=p.trimester, cols=_COLS)
    if not rows:
        return {"available": False, "note": "no reports for this scope"}
    att_all = [s.get("student_attendance") for r in rows for s in _subjects(r)]
    acc = accuracy_rows(svc, campus=p.campus, batch=p.batch, trimester=p.trimester,
                        cols="overall_pct")
    return {
        "cohort_size": len(rows),
        "mean_subjects_at_or_above_average": _mean([r.get("above_average_count") for r in rows]),
        "mean_subjects_per_student": _mean([r.get("subjects_count") for r in rows]),
        "mean_attendance_pct": _mean(att_all),
        "mean_report_accuracy_pct": _mean([a.get("overall_pct") for a in acc]) if acc else None,
        "top_strengths": [{"subject": s, "students": n} for s, n in
                          Counter(r.get("strongest_subject") for r in rows
                                  if r.get("strongest_subject")).most_common(8)],
        "personal_pattern_kinds": dict(Counter(r.get("personal_pattern_kind") for r in rows
                                               if r.get("personal_pattern_kind"))),
    }


def _subject_impl(svc, p: SubjectParams) -> dict:
    rows = report_rows(svc, campus=p.campus, batch=p.batch, trimester=p.trimester, cols=_COLS)
    if not rows:
        return {"available": False, "note": "no reports for this scope"}
    agg = defaultdict(lambda: {"scores": [], "att": [], "class_avg": None, "class_att": None})
    for r in rows:
        for s in _subjects(r):
            name = s.get("name")
            if not name:
                continue
            if p.subject and p.subject.lower() not in name.lower():
                continue
            a = agg[name]
            if isinstance(s.get("student_score"), (int, float)):
                a["scores"].append(s["student_score"])
            if isinstance(s.get("student_attendance"), (int, float)):
                a["att"].append(s["student_attendance"])
            a["class_avg"] = s.get("class_marks_average")
            a["class_att"] = s.get("class_attendance_average")
    subjects = []
    for name, a in agg.items():
        sc = a["scores"]
        subjects.append({
            "subject": name, "students": len(sc),
            "class_average_marks": a["class_avg"], "mean_student_marks": _mean(sc),
            "pass_rate_pct": (round(100 * sum(1 for x in sc if x >= 40) / len(sc), 1) if sc else None),
            "mean_attendance": _mean(a["att"]), "class_attendance_average": a["class_att"],
        })
    subjects.sort(key=lambda x: (x["mean_student_marks"] is None, x["mean_student_marks"] or 0))
    return {"subject_count": len(subjects), "subjects": subjects,
            "note": "Sorted weakest-mean first (intervention priority)."}


def _compare_impl(svc, p: CompareParams) -> dict:
    scope = svc.campus_scope(None)  # all granted campuses
    campuses = scope if scope is not None else None
    rows = report_rows(svc, batch=p.batch, trimester=p.trimester, cols=_COLS)
    acc = accuracy_rows(svc, batch=p.batch, trimester=p.trimester, cols="campus,overall_pct")
    by_c = defaultdict(list)
    for r in rows:
        by_c[r.get("campus")].append(r)
    acc_by_c = defaultdict(list)
    for a in acc:
        acc_by_c[a.get("campus")].append(a.get("overall_pct"))
    out = []
    for c, rs in by_c.items():
        att = [s.get("student_attendance") for r in rs for s in _subjects(r)]
        out.append({"campus": c, "cohort_size": len(rs),
                    "mean_subjects_at_or_above_average": _mean([r.get("above_average_count") for r in rs]),
                    "mean_attendance_pct": _mean(att),
                    "mean_report_accuracy_pct": _mean(acc_by_c.get(c, []))})
    out.sort(key=lambda x: x["campus"] or "")
    if not out:
        return {"available": False, "note": "no reports for this batch/trimester in your scope"}
    return {"batch": p.batch, "trimester": str(p.trimester), "campuses": out}


def register(mcp, get_service):
    @mcp.tool(title="Campus Overview", annotations=READONLY_ANNOTATIONS)
    async def campus_overview(params: ScopeParams) -> dict:
        """
        WHAT: Cohort snapshot for a campus/batch/trimester — size, mean subjects at/above average,
        mean attendance, mean report accuracy, top strength subjects, and personal-pattern mix.
        USE WHEN they say: 'how is indore doing', 'cohort summary', 'overview of 2024-26 T5',
        'campus stats'.
        DO NOT USE WHEN they want per-subject detail (subject_performance) or a specific student.
        RETURNS: cohort_size + means + top_strengths. available:false if no reports.
        """
        return _overview_impl(await get_service(), params)

    @mcp.tool(title="Subject Performance", annotations=READONLY_ANNOTATIONS)
    async def subject_performance(params: SubjectParams) -> dict:
        """
        WHAT: Per-subject cohort performance — class average, mean student marks, pass rate, mean
        attendance — sorted weakest first as an intervention priority list.
        USE WHEN they say: 'which subjects are weak', 'subject-wise performance', 'where are
        students struggling', 'pass rate in <subject>'.
        DO NOT USE WHEN they want one student or the overall campus roll-up.
        RETURNS: subjects with class_average, mean_student_marks, pass_rate_pct, attendance.
        """
        return _subject_impl(await get_service(), params)

    @mcp.tool(title="Cohort Compare", annotations=READONLY_ANNOTATIONS)
    async def cohort_compare(params: CompareParams) -> dict:
        """
        WHAT: Side-by-side comparison of your campuses for a batch/trimester — cohort size, mean
        subjects above average, mean attendance, mean report accuracy.
        USE WHEN they say: 'compare campuses', 'which campus is doing best', 'indore vs lucknow',
        'campus leaderboard'.
        DO NOT USE WHEN scoped to one campus (use campus_overview).
        RETURNS: campuses[] each with its means. available:false if none in scope.
        """
        return _compare_impl(await get_service(), params)
