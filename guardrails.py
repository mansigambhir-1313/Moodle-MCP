"""Guardrails shared by every tool — budgets, uniform misses, secret stripping, scoping."""

LIST_PREVIEW_CHARS = 400
SNIPPET_CHARS = 600
MAX_LIST = 50

# Keys that must never leave the server (internal ids, storage paths, hashes, tokens).
_SECRET_KEYS = {
    "run_id", "report_job_id", "report_id", "input_hash", "student_hash",
    "html_object_key", "pdf_object_key", "html_sha256", "pdf_sha256", "student_email",
}
_SECRET_SUFFIXES = ("_object_key", "_sha256", "_token", "_secret", "_hash")


def not_found(what: str = "record") -> dict:
    """Uniform miss — identical for 'does not exist' and 'out of your campus scope'."""
    return {"found": False, "note": f"No {what} found for your access scope."}


def strip_secrets(row: dict) -> dict:
    if not isinstance(row, dict):
        return row
    return {k: v for k, v in row.items()
            if k not in _SECRET_KEYS and not k.endswith(_SECRET_SUFFIXES)}


def truncate(text, limit: int = LIST_PREVIEW_CHARS):
    if not isinstance(text, str):
        return text
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def clamp_limit(n, default: int = 20) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, MAX_LIST))


def scope_campuses(requested, allowed):
    """Intersect a requested campus with the token's allowed set.
    allowed=None means all campuses. Returns a list to filter on, or None for 'all'."""
    if allowed is None:
        return [requested] if requested else None
    allowed = list(allowed)
    if not requested:
        return allowed
    return [requested] if requested in allowed else []  # [] => out of scope, yields empty results
