"""Read-only, campus-scoped data-access layer for the Moodle Reports MCP.

Unlike the student MCP (per-user RLS), report data is institutional, so a single bounded
supabase client is reused process-wide; the tenant boundary is the token's allowed-campus set,
applied by every tool. Service role key stays server-side and is never exposed to the host.
"""
import logging

from supabase import Client, create_client

from cache import TTLCache
from config import settings

log = logging.getLogger(__name__)

_run_cache = TTLCache(maxsize=64, ttl=300)
_client_singleton: Client | None = None


def _client() -> Client:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = create_client(
            settings.supabase_url, settings.supabase_service_role_key)
    return _client_singleton


class MoodleService:
    """Per-request handle: the shared client + this caller's campus scope."""

    def __init__(self, client: Client, principal: dict):
        self.client = client
        self.principal = principal
        self.allowed_campuses = principal.get("campuses")  # None == all campuses

    # --- campus scoping ---------------------------------------------------
    def campus_scope(self, requested: str | None):
        """Return the campus list to filter on (None == all), intersecting the request with the
        token's grant. Returns [] when the requested campus is outside the grant (empty results)."""
        if self.allowed_campuses is None:
            return [requested] if requested else None
        allowed = list(self.allowed_campuses)
        if not requested:
            return allowed
        return [requested] if requested in allowed else []

    def apply_campus(self, query, col: str = "campus", requested: str | None = None):
        scope = self.campus_scope(requested)
        if scope is None:
            return query
        return query.in_(col, scope)

    # --- pinned run -------------------------------------------------------
    def latest_run(self, campus: str, batch: str, purpose: str | None = None):
        purpose = purpose or settings.report_purpose
        key = (campus, batch, purpose)
        hit = _run_cache.get(key)
        if hit is not None:
            return hit or None
        res = (self.client.table("extraction_runs")
               .select("run_id,finished_at")
               .eq("campus", campus).eq("batch", batch)
               .eq("status", "completed").eq("purpose", purpose)
               .not_.is_("finished_at", "null")
               .order("finished_at", desc=True).limit(1).execute()).data
        rid = res[0]["run_id"] if res else None
        _run_cache.set(key, rid or "")
        return rid


def create_service(principal: dict) -> MoodleService:
    return MoodleService(_client(), principal)
