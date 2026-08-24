"""Subject / course data — the catalog of subjects and a whole subject's marks + attendance across
the cohort, from the raw tables. Read-only, campus-scoped."""
from collections import defaultdict

from pydantic import BaseModel, Field

from annotations import READONLY_ANNOTATIONS
from guardrails import not_found
from tools.common import (attendance_for, attendance_pct, courses_for, marks_for, pct)


class ScopeParams(BaseModel):
    campus: str = Field(description="campus (within your grant)")
    batch: str = Field(description="batch e.g. '2024-26'")
    trimester: str | None = Field(default=None, description="restrict to one trimester")


class SubjectParams(ScopeParams):
    subject: str = Field(description="subject-name fragment, e.g. 'Wealth Management'")


def _run(svc, p):
    if svc.campus_scope(p.campus) == []:
        return None
    return svc.latest_run(p.campus, p.batch)


def _mean(xs, nd=1):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), nd) if xs else None


def _list_impl(svc, p: ScopeParams) -> dict:
    run_id = _run(svc, p)
    if not run_id:
        return {"available": False, "note": "no completed run for this scope"}
    courses = courses_for(svc, run_id, p.trimester)
    enr = (svc.client.table("enrolments").select("course_id,student_id")
           .eq("run_id", run_id).limit(100000).execute()).data or []
    counts = defaultdict(set)
    for e in enr:
        counts[e["course_id"]].add(e["student_id"])
    by_subject = defaultdict(lambda: {"trimester": None, "sections": 0, "students": set()})
    for cid, meta in courses.items():
        s = by_subject[meta["subject"]]
        s["trimester"] = meta["trimester"]
        s["sections"] += 1
        s["students"] |= counts.get(cid, set())
    subjects = [{"subject": name, "trimester": v["trimester"], "sections": v["sections"],
                 "students": len(v["students"])} for name, v in by_subject.items()]
    subjects.sort(key=lambda x: (str(x["trimester"]), x["subject"]))
    return {"campus": p.campus, "batch": p.batch, "subject_count": len(subjects),
            "subjects": subjects}


def _match_courses(courses, fragment):
    fl = fragment.lower()
    return {cid: m for cid, m in courses.items() if fl in m["subject"].lower()}


def _subject_impl(svc, p: SubjectParams) -> dict:
    run_id = _run(svc, p)
    if not run_id:
        return {"available": False, "note": "no completed run for this scope"}
    courses = _match_courses(courses_for(svc, run_id, p.trimester), p.subject)
    if not courses:
        return not_found("subject")
    cids = list(courses)
    marks = marks_for(svc, run_id, course_ids=cids)
    att = attendance_for(svc, run_id, course_ids=cids)
    # per-student overall subject mark (graded components), then cohort distribution
    per_student = defaultdict(lambda: [0.0, 0.0])
    comp = defaultdict(lambda: {"obt": [], "kind": None})
    for m in marks:
        if m.get("graded") and isinstance(m.get("obtained_score"), (int, float)) \
                and isinstance(m.get("max_score"), (int, float)):
            per_student[m["student_id"]][0] += m["obtained_score"]
            per_student[m["student_id"]][1] += m["max_score"]
        c = comp[m.get("component_label")]
        c["kind"] = m.get("kind")
        c["obt"].append(pct(m.get("obtained_score"), m.get("max_score")))
    student_pcts = [pct(o, mx) for o, mx in per_student.values() if mx]
    present, total, apct = attendance_pct(att)
    subject_name = next(iter(courses.values()))["subject"]
    return {"subject": subject_name, "campus": p.campus, "batch": p.batch,
            "trimester": next(iter(courses.values()))["trimester"],
            "sections": len(cids), "students_graded": len(student_pcts),
            "mean_mark_pct": _mean(student_pcts),
            "pass_rate_pct": (round(100 * sum(1 for x in student_pcts if x >= 40) / len(student_pcts), 1)
                              if student_pcts else None),
            "cohort_attendance_pct": apct,
            "components": [{"component": name, "kind": c["kind"],
                            "mean_pct": _mean(c["obt"]), "n": len(c["obt"])}
                           for name, c in comp.items()]}


def register(mcp, get_service):
    @mcp.tool(title="List Subjects", annotations=READONLY_ANNOTATIONS)
    async def list_subjects(params: ScopeParams) -> dict:
        """
        WHAT: The subjects/courses offered for a campus/batch (optionally one trimester), each with
        its trimester, number of sections, and enrolled-student count.
        USE WHEN they say: 'what subjects are there', 'list courses in 2024-26', 'which subjects in
        trimester 5', 'how many sections of <subject>'.
        DO NOT USE WHEN they want marks in a subject (subject_performance) or a student.
        RETURNS: subjects[] (subject, trimester, sections, students).
        """
        return _list_impl(await get_service(), params)

    @mcp.tool(title="Subject Performance", annotations=READONLY_ANNOTATIONS)
    async def subject_performance(params: SubjectParams) -> dict:
        """
        WHAT: A whole subject's data across the cohort — mean student mark %, pass rate, cohort
        attendance, and a per-component mean — computed from the raw marks/attendance rows.
        USE WHEN they say: 'how did the class do in <subject>', 'pass rate in wealth management',
        'component-wise averages for <subject>', 'subject performance'.
        DO NOT USE WHEN they want one student (get_student) or the subject list (list_subjects).
        RETURNS: mean_mark_pct, pass_rate_pct, cohort_attendance_pct, components[].
        """
        return _subject_impl(await get_service(), params)
