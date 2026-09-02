"""DB-backed faculty access registry (table: mcp_faculty).

Why a table and not the MCP_FACULTY env var: ~500 faculty with per-campus scopes
don't fit an env var, and the roster changes without redeploys. The env var stays
as a break-glass ADMIN override (checked before this module — see
security.principal_from_claims), so a bad DB row or an outage can never lock the
administrator out.

Threat model notes:
* 3,144 STUDENTS share the @jaipuria.ac.in Google domain, so the domain gate can
  never grant anything by itself. Every grant must be explicit, and any email
  found in the student roster is HARD-DENIED here even if someone mistakenly
  inserts it into mcp_faculty.
* Everything fails CLOSED: a DB error, a malformed row, an inactive row, or an
  unparseable campuses value all resolve to "no access" (with a stale-cache
  grace for transient DB blips, so a 2-second Supabase hiccup doesn't kick out
  500 signed-in users mid-session).
* Lookups are cached (bounded TTL caches) so steady-state cost is ~1 small
  SELECT per user per minute, regardless of tool-call volume.

The MCP's DB credential is reporting_readonly: it can SELECT this table (RLS
policy) and cannot write it. Roster changes happen out-of-band with the service
role — see OPERATIONS.md for the bulk-load runbook.
"""
import logging

from cache import TTLCache

log = logging.getLogger("moodle-mcp.faculty")

_GRANT_TTL = 60.0     # how long a grant/denial is believed before re-checking
_STUDENT_TTL = 600.0  # roster membership changes rarely
_MISS = "__no_grant__"

_grants = TTLCache(maxsize=4096, ttl=_GRANT_TTL)
_stale_grants = TTLCache(maxsize=4096, ttl=3600.0)  # last-known-good, outage grace
_students = TTLCache(maxsize=8192, ttl=_STUDENT_TTL)


def _sb():
    from supabase_client import _client
    return _client()


# --- raw fetches (module-level so tests can monkeypatch them) ----------------
def _fetch_faculty_row(email: str):
    rows = (_sb().table("mcp_faculty").select("name,campuses,active")
            .eq("email", email).limit(1).execute()).data
    return rows[0] if rows else None


def _fetch_student_hit(email: str) -> bool:
    rows = (_sb().table("students").select("student_id")
            .ilike("student_email", email).limit(1).execute()).data
    return bool(rows)


# --- public API ---------------------------------------------------------------
def normalize_campuses(v):
    """mcp_faculty.campuses -> principal 'campuses' value: None for "all", a
    non-empty list of lowercase strings for a scoped grant, or the sentinel
    'invalid' for anything else (deny — a malformed row must never widen access)."""
    if isinstance(v, str) and v.strip().lower() == "all":
        return None
    if isinstance(v, list) and v and all(isinstance(x, str) and x.strip() for x in v):
        return [x.strip().lower() for x in v]
    return "invalid"


def faculty_grant(email: str):
    """Active mcp_faculty row -> {'name': ..., 'campuses': None|[...]}, else None.
    Fail-closed on any error, with last-known-good grace for transient DB blips."""
    email = (email or "").strip().lower()
    if not email:
        return None
    hit = _grants.get(email)
    if hit is not None:
        return None if hit == _MISS else dict(hit)
    try:
        row = _fetch_faculty_row(email)
    except Exception:  # noqa: BLE001 — DB down != access granted
        stale = _stale_grants.get(email)
        if stale is not None:
            log.warning("faculty lookup failed for %s — serving last-known-good grant", email)
            return dict(stale)
        log.warning("faculty lookup failed for %s — fail-closed deny", email, exc_info=True)
        return None
    if not row or row.get("active") is not True:
        _grants.set(email, _MISS)
        return None
    campuses = normalize_campuses(row.get("campuses"))
    if campuses == "invalid":
        log.error("mcp_faculty row for %s has malformed campuses %r — denying",
                  email, row.get("campuses"))
        _grants.set(email, _MISS)
        return None
    grant = {"name": row.get("name"), "campuses": campuses}
    _grants.set(email, grant)
    _stale_grants.set(email, grant)
    return dict(grant)


def is_student(email: str) -> bool:
    """True when the email appears in the student roster — those accounts are
    hard-denied regardless of any mcp_faculty row. On a DB error this returns
    True (deny): the env MCP_FACULTY override path never consults this, so the
    administrator always retains break-glass access."""
    email = (email or "").strip().lower()
    if not email:
        return True
    hit = _students.get(email)
    if hit is not None:
        return hit
    try:
        found = _fetch_student_hit(email)
    except Exception:  # noqa: BLE001
        log.warning("student-roster check failed for %s — fail-closed deny", email)
        return True
    _students.set(email, found)
    if found:
        log.warning("access denied: %s is in the student roster", email)
    return found
