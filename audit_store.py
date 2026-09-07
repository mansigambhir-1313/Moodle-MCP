"""Privacy-safe, append-only audit delivery for authenticated MCP tool calls."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid

import httpx

from config import settings

log = logging.getLogger("moodle-mcp.audit")

_http: httpx.AsyncClient | None = None


def _subject(value: str | None) -> str | None:
    """Pseudonymise an identifier before it leaves the MCP process."""
    if not value or not settings.audit_hmac_key:
        return None
    return hmac.new(
        settings.audit_hmac_key.encode(), value.strip().lower().encode(), hashlib.sha256
    ).hexdigest()


def principal_subject(principal) -> str:
    if not isinstance(principal, dict):
        return "anonymous"
    stable = principal.get("email") or principal.get("sub") or principal.get("name")
    return _subject(str(stable)) or "unknown"


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=3.0)
    return _http


async def record_tool_call(
    *,
    tool: str,
    principal,
    ok: bool | None,
    started: float,
    headers: dict | None = None,
    scope: str | None = None,
    error_code: str | None = None,
) -> bool:
    """Insert one sanitized event and report whether durable delivery succeeded.

    ``ok=None`` is a pre-execution attempt record. In fail-closed audit mode that
    record must land before any data access or report generation begins.
    """
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    user_subject = principal_subject(principal)
    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    request_id = headers.get("x-request-id") or str(uuid.uuid4())

    if not settings.audit_enabled():
        log.info("audit tool=%s subject=%s ok=%s scope=%s duration_ms=%s error=%s",
                 tool, user_subject[:16], "attempt" if ok is None else int(ok),
                 scope or "-", duration_ms,
                 error_code or "-")
        return False

    payload = {
        "p_event_id": str(uuid.uuid4()),
        "p_user_subject": user_subject,
        "p_tool_name": tool[:128],
        "p_campus_scope": (scope or "")[:64] or None,
        "p_outcome": "attempt" if ok is None else ("success" if ok else "failure"),
        "p_error_code": (error_code or "")[:64] or None,
        "p_duration_ms": duration_ms,
        "p_request_id": request_id[:128],
        "p_session_subject": _subject(headers.get("mcp-session-id")),
        "p_client_subject": _subject(headers.get("user-agent")),
        "p_metadata": {"server_version": settings.server_version},
    }
    base = settings.supabase_url.rstrip("/")
    api_key = settings.supabase_anon_key or settings.supabase_audit_key
    try:
        response = await _client().post(
            f"{base}/rest/v1/rpc/record_mcp_tool_call",
            headers={
                "apikey": api_key,
                "Authorization": f"Bearer {settings.supabase_audit_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 — audit outage never changes tool outcome
        log.warning("audit delivery failed: %s", type(exc).__name__)
        return False
