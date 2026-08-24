"""Shared query helpers for the tool modules — all read-only and campus-scoped."""

CATALOG = "student_reports"
ACCURACY = "report_accuracy"


def report_rows(svc, *, campus=None, batch=None, trimester=None, cols="*",
                status="ready", limit=100000):
    """Scoped rows from the report catalog (student_reports). Campus filter is always applied."""
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


def paged(query_factory, page: int = 1000, cap: int = 50000):
    """Fetch all rows past PostgREST's 1000-row cap via .range() pagination.
    query_factory(offset, limit) must return an executed response's .data list."""
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


def student_label(row: dict) -> dict:
    """Uniform public identity for a student — name + enrolment id, never internal ids."""
    return {"student_id": row.get("student_id"),
            "name": (row.get("full_name") or row.get("first_name") or row.get("student_id"))}
