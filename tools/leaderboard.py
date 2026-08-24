"""Recognition & strengths — top performers, cohort strength map, most-improved.
Derived from the report catalog's deterministic figures. Read-only, campus-scoped."""
from collections import Counter

from pydantic import BaseModel, Field

from annotations import READONLY_ANNOTATIONS
from guardrails import clamp_limit
from tools.common import report_rows, student_label

_COLS = "student_id,full_name,first_name,campus,batch,trimester,above_average_count,subjects_count,strongest_subject,evidence_packet"


class TopParams(BaseModel):
    campus: str | None = None
    batch: str | None = None
    trimester: str | None = None
    limit: int = Field(default=10, description="how many top students, 1-50")


class ScopeParams(BaseModel):
    campus: str | None = None
    batch: str | None = None
    trimester: str | None = None


def _mean_delta(row):
    subs = (row.get("evidence_packet") or {}).get("subjects", []) or []
    ds = [s.get("score_delta") for s in subs if isinstance(s.get("score_delta"), (int, float))]
    return round(sum(ds) / len(ds), 2) if ds else 0.0


def _top_impl(svc, p: TopParams) -> dict:
    rows = report_rows(svc, campus=p.campus, batch=p.batch, trimester=p.trimester, cols=_COLS)
    ranked = sorted(rows, key=lambda r: (r.get("above_average_count") or 0, _mean_delta(r)),
                    reverse=True)[:clamp_limit(p.limit)]
    return {"count": len(ranked),
            "students": [{**student_label(r), "campus": r.get("campus"),
                          "subjects_at_or_above_average": r.get("above_average_count"),
                          "subjects_total": r.get("subjects_count"),
                          "mean_margin_vs_class": _mean_delta(r),
                          "strongest_subject": r.get("strongest_subject")} for r in ranked]}


def _strength_impl(svc, p: ScopeParams) -> dict:
    rows = report_rows(svc, campus=p.campus, batch=p.batch, trimester=p.trimester, cols=_COLS)
    if not rows:
        return {"available": False, "note": "no reports for this scope"}
    strong = Counter(r.get("strongest_subject") for r in rows if r.get("strongest_subject"))
    # cohort-weak subjects: mean class-relative delta per subject, most-negative first
    from collections import defaultdict
    deltas = defaultdict(list)
    for r in rows:
        for s in (r.get("evidence_packet") or {}).get("subjects", []) or []:
            if isinstance(s.get("score_delta"), (int, float)) and s.get("name"):
                deltas[s["name"]].append(s["score_delta"])
    weak = sorted(((n, round(sum(v) / len(v), 2)) for n, v in deltas.items() if v),
                  key=lambda kv: kv[1])[:6]
    return {"cohort_size": len(rows),
            "cohort_strength_subjects": [{"subject": s, "students_strongest_here": n}
                                         for s, n in strong.most_common(8)],
            "cohort_weak_subjects": [{"subject": n, "mean_margin_vs_class": d} for n, d in weak],
            "note": "Strength = named as a student's strongest; weak = most-negative mean margin."}


def _improved_impl(svc, p: ScopeParams) -> dict:
    # Improvement needs ≥2 scored trimesters for the same students; the catalog stores one
    # trimester per report, so cross-trimester requires both to be generated. Degrade honestly.
    tris = {r.get("trimester") for r in report_rows(svc, campus=p.campus, batch=p.batch,
            cols="trimester")}
    tris = sorted(t for t in tris if t)
    if len(tris) < 2:
        return {"available": False, "trimesters_present": tris,
                "note": "Most-improved needs at least two generated trimesters for this cohort; "
                        "only one is present. Generate an earlier trimester to enable this."}
    return {"available": False, "trimesters_present": tris,
            "note": "Two trimesters detected — cross-trimester improvement ranking is a planned "
                    "extension (join on student_id across trimesters)."}


def register(mcp, get_service):
    @mcp.tool(title="Top Performers", annotations=READONLY_ANNOTATIONS)
    async def top_performers(params: TopParams) -> dict:
        """
        WHAT: The strongest students in a scope, ranked by how many subjects sit at/above the class
        average and their mean margin vs the class.
        USE WHEN they say: 'top students', 'who is doing best', 'leaderboard', 'highest performers
        in indore T5', 'who to recognise'.
        DO NOT USE WHEN they want at-risk students (at_risk_students) or one student.
        RETURNS: ranked students with subjects_at_or_above_average and mean_margin_vs_class.
        """
        return _top_impl(await get_service(), params)

    @mcp.tool(title="Strength Map", annotations=READONLY_ANNOTATIONS)
    async def strength_map(params: ScopeParams) -> dict:
        """
        WHAT: Where the cohort is strong vs weak — subjects most often a student's strongest, and
        the subjects with the most-negative mean margin vs class.
        USE WHEN they say: 'what are we good at', 'cohort strengths and weaknesses', 'which subjects
        lift or drag the cohort', 'strength map'.
        DO NOT USE WHEN they want per-subject pass rates (subject_performance) or one student.
        RETURNS: cohort_strength_subjects + cohort_weak_subjects. available:false if none.
        """
        return _strength_impl(await get_service(), params)

    @mcp.tool(title="Most Improved", annotations=READONLY_ANNOTATIONS)
    async def most_improved(params: ScopeParams) -> dict:
        """
        WHAT: Students who improved the most across trimesters (needs ≥2 generated trimesters).
        USE WHEN they say: 'who improved', 'most improved', 'biggest gains this term'.
        DO NOT USE WHEN only one trimester exists — it returns available:false with guidance.
        RETURNS: ranked improvement, or available:false + which trimesters are present.
        """
        return _improved_impl(await get_service(), params)
