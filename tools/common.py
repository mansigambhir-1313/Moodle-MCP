"""Shared query helpers — read-only, campus-scoped. Data-first: the primary source is the raw
Moodle tables (students, courses, enrolments, marks, attendance_sessions); reports/accuracy are a
secondary layer."""

CATALOG = "student_reports"
ACCURACY = "report_accuracy"
MARKS = "marks"
ATTENDANCE = "attendance_sessions"


# --- course-code helpers ------------------------------------------------------
def course_trimester(code: str):
    """Trimester digit from a course code 'subjectnum_batch_TRIMESTER_section' (e.g. 30503_26_3_C)."""
    parts = (code or "").split("_")
    return parts[2] if len(parts) >= 3 and parts[2].isdigit() else None


def course_section(code: str):
    """Section token from a course code. Robust across both layouts:
      old 4-field 'subjnum_batch_trim_SECTION'        e.g. 30503_26_3_C     -> 'C'
      new 5-field 'subjnum_batch_trim_SECTION_ABBR'   e.g. 20201_27_1_A_AFB -> 'A'
    Section is always field 4 ([3]); the older format just has it as the last field.
    (Using rsplit('_')[-1] grabbed the subject abbreviation on the new format.)"""
    parts = (code or "").split("_")
    if len(parts) >= 4:
        return parts[3]
    return parts[-1] if parts and parts[0] else None


def clean_subject(name: str) -> str:
    """'Business Research Methods - C' -> 'Business Research Methods' (strip the section suffix)."""
    n = (name or "").strip()
    return n.rsplit(" - ", 1)[0].strip() if " - " in n else n


def pct(obtained, maximum):
    try:
        return round(100 * float(obtained) / float(maximum), 1) if maximum else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# --- run + course resolution --------------------------------------------------
def courses_for(svc, run_id, trimester=None) -> dict:
    """course_id -> {code, name, subject, trimester} for a run, optionally one trimester."""
    rows = (svc.client.table("courses").select("course_id,course_code,course_name")
            .eq("run_id", run_id).limit(100000).execute()).data or []
    out = {}
    for c in rows:
        t = course_trimester(c["course_code"])
        if trimester and t != str(trimester):
            continue
        out[c["course_id"]] = {"code": c["course_code"], "name": c["course_name"],
                               "subject": clean_subject(c["course_name"]), "trimester": t}
    return out


def find_student(svc, student_id):
    """Locate a student in the roster within the caller's campus scope. Returns the row
    (campus, batch, student_name) or None. Uses the students table, so it covers EVERY ingested
    student — not only those with a generated report."""
    q = svc.client.table("students").select("student_id,student_name,campus,batch,section_group")
    q = svc.apply_campus(q, requested=None)
    rows = (q.eq("student_id", student_id).limit(1).execute()).data or []
    return rows[0] if rows else None


# --- paged fetch past the 1000-row PostgREST cap ------------------------------
def paged(query_factory, page: int = 1000, cap: int = 100000):
    out, offset = [], 0
    while offset < cap:
        batch = query_factory(offset, page)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return out


def marks_for(svc, run_id, *, student_id=None, course_ids=None):
    def factory(off, lim):
        q = (svc.client.table(MARKS)
             .select("student_id,course_id,component_label,kind,graded,obtained_score,max_score")
             .eq("run_id", run_id))
        if student_id:
            q = q.eq("student_id", student_id)
        if course_ids is not None:
            q = q.in_("course_id", course_ids)
        return (q.range(off, off + lim - 1).execute()).data or []
    return paged(factory)


def attendance_for(svc, run_id, *, student_id=None, course_ids=None):
    def factory(off, lim):
        q = (svc.client.table(ATTENDANCE).select("student_id,course_id,status_norm")
             .eq("run_id", run_id))
        if student_id:
            q = q.eq("student_id", student_id)
        if course_ids is not None:
            q = q.in_("course_id", course_ids)
        return (q.range(off, off + lim - 1).execute()).data or []
    return paged(factory)


def attendance_pct(rows) -> tuple:
    """(present, total, pct) from attendance_sessions rows (status_norm 'present'/'absent'/...)."""
    total = len(rows)
    present = sum(1 for r in rows if (r.get("status_norm") or "").lower() == "present")
    return present, total, (round(100 * present / total, 1) if total else None)


# --- report / accuracy (secondary) -------------------------------------------
def report_rows(svc, *, campus=None, batch=None, trimester=None, cols="*",
                status="ready", limit=100000):
    q = svc.client.table(CATALOG).select(cols)
    q = svc.apply_campus(q, requested=campus)
    if batch:
        q = q.eq("batch", batch)
    if trimester:
        q = q.eq("trimester", str(trimester))
    if status:
        q = q.eq("status", status)
    return (q.limit(limit).execute()).data or []


def one_report(svc, student_id, *, campus=None, batch=None, trimester=None, cols="*"):
    q = svc.client.table(CATALOG).select(cols).eq("student_id", student_id)
    q = svc.apply_campus(q, requested=campus)
    if batch:
        q = q.eq("batch", batch)
    if trimester:
        q = q.eq("trimester", str(trimester))
    rows = (q.order("generated_at", desc=True).limit(1).execute()).data or []
    return rows[0] if rows else None


def accuracy_rows(svc, *, campus=None, batch=None, trimester=None, label=None, cols="*",
                  limit=100000):
    q = svc.client.table(ACCURACY).select(cols)
    q = svc.apply_campus(q, requested=campus)
    if batch:
        q = q.eq("batch", batch)
    if trimester:
        q = q.eq("trimester", str(trimester))
    if label:
        q = q.eq("overall_label", label)
    return (q.limit(limit).execute()).data or []


def student_label(row: dict) -> dict:
    return {"student_id": row.get("student_id"),
            "name": (row.get("student_name") or row.get("full_name")
                     or row.get("first_name") or row.get("student_id"))}


# --- cohort rollup over raw marks + attendance (cached per run) ---------------
from cache import TTLCache  # noqa: E402
from collections import defaultdict  # noqa: E402

_rollup_cache = TTLCache(maxsize=8, ttl=300)
_marks_cache = TTLCache(maxsize=8, ttl=300)


def scope_marks(svc, run_id, course_ids):
    """All raw marks rows for a set of courses, cached per (run, course-set)."""
    key = (run_id, tuple(sorted(course_ids)))
    hit = _marks_cache.get(key)
    if hit is not None:
        return hit
    rows = marks_for(svc, run_id, course_ids=list(course_ids))
    _marks_cache.set(key, rows)
    return rows


def cohort_rollup(svc, run_id, courses) -> dict:
    """Per-student rollup from the raw tables for a scope: overall graded-mark %, recorded zeros,
    overall attendance %, and subjects below 75% attendance. Cached per (run, trimester-set)."""
    key = (run_id, tuple(sorted(courses)))
    hit = _rollup_cache.get(key)
    if hit is not None:
        return hit
    cids = list(courses)
    names = {r["student_id"]: r.get("student_name") for r in
             (svc.client.table("students").select("student_id,student_name")
              .eq("run_id", run_id).limit(100000).execute()).data or []}
    marks = marks_for(svc, run_id, course_ids=cids)
    att = attendance_for(svc, run_id, course_ids=cids)
    agg = defaultdict(lambda: {"obt": 0.0, "max": 0.0, "zeros": [], "att": defaultdict(list)})
    for m in marks:
        a = agg[m["student_id"]]
        if m.get("graded") and isinstance(m.get("obtained_score"), (int, float)) \
                and isinstance(m.get("max_score"), (int, float)):
            a["obt"] += m["obtained_score"]; a["max"] += m["max_score"]
            if m["obtained_score"] == 0:
                a["zeros"].append(m.get("component_label"))
    for r in att:
        agg[r["student_id"]]["att"][r["course_id"]].append(r.get("status_norm"))
    out = {}
    for sid, a in agg.items():
        att_all = [s for rows in a["att"].values() for s in rows]
        pres = sum(1 for s in att_all if (s or "").lower() == "present")
        low_subj = []
        for cid, rows in a["att"].items():
            p, t, ap = attendance_pct([{"status_norm": s} for s in rows])
            if ap is not None and ap < 75:
                low_subj.append((courses.get(cid, {}).get("subject"), ap))
        out[sid] = {"name": names.get(sid, sid), "mark_pct": pct(a["obt"], a["max"]),
                    "zeros": [z for z in a["zeros"] if z][:8],
                    "attendance_pct": (round(100 * pres / len(att_all), 1) if att_all else None),
                    "low_attendance_subjects": low_subj}
    _rollup_cache.set(key, out)
    return out
