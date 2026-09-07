"""Regression checks for the 5,000-user security foundation."""

import asyncio
import hashlib
import hmac
import os
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-data-key")

from audit_store import principal_subject  # noqa: E402
from config import settings  # noqa: E402
from oauth_compat import _safe_redirect_uri  # noqa: E402
from security import HostGuard, SecurityHeaders, SharedRateLimiter  # noqa: E402
from tools.actions import (CreateReportParams, _queued, _safe_report_url,
                           _signed_agent_headers)  # noqa: E402
from tools.reports import ReportParams  # noqa: E402


def test_normalization_and_link_allowlist():
    assert CreateReportParams(campus=" NoIdA ").campus == "noida"
    assert ReportParams(student_id="JN24PG001", campus=" NoIdA ").campus == "noida"
    assert _safe_report_url("https://reports.tryrehearsal.ai/s/opaque")
    assert not _safe_report_url("https://evil.example/s/opaque")
    assert not _safe_report_url("https://reports.tryrehearsal.ai/admin")
    assert not _safe_report_url("https://reports.tryrehearsal.ai/s/opaque?next=/admin")
    assert not _safe_report_url("https://reports.tryrehearsal.ai:444/s/opaque")
    assert not _safe_report_url("https://reports.tryrehearsal.ai/s/../admin")


def test_redirect_allowlist():
    assert _safe_redirect_uri("https://chatgpt.com/callback", {"chatgpt.com"})
    assert not _safe_redirect_uri("https://evil.example/callback", {"chatgpt.com"})
    assert _safe_redirect_uri("http://127.0.0.1:55173/callback", {"chatgpt.com"})


def test_subject_is_pseudonymous():
    old = settings.audit_hmac_key
    settings.audit_hmac_key = "test-only-audit-key"
    try:
        subject = principal_subject({"email": "faculty@jaipuria.ac.in"})
        assert "faculty" not in subject
        assert "@" not in subject
        assert len(subject) == 64
    finally:
        settings.audit_hmac_key = old


def test_local_rate_fallback():
    limiter = SharedRateLimiter(1, 60, maxkeys=8)

    async def run():
        assert (await limiter.allow("user"))[0]
        assert not (await limiter.allow("user"))[0]

    asyncio.run(run())


def test_host_and_security_headers():
    sent = []

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = SecurityHeaders(HostGuard(inner, ["mcp.example.com"]))

    async def run(host, path="/mcp"):
        sent.clear()
        async def capture(message):
            sent.append(message)
        await app({"type": "http", "path": path,
                   "headers": [(b"host", host.encode())]}, None, capture)
        return list(sent)

    accepted = asyncio.run(run("mcp.example.com"))
    headers = dict(accepted[0]["headers"])
    assert accepted[0]["status"] == 200
    assert headers[b"cache-control"] == b"no-store"
    rejected = asyncio.run(run("raw-origin.example"))
    assert rejected[0]["status"] == 421
    health = asyncio.run(run("platform-health.internal", "/health"))
    assert health[0]["status"] == 200


def test_report_queue_contract_and_signature():
    old = settings.agent_shared_secret
    settings.agent_shared_secret = "s" * 40
    try:
        url = "https://reports.example/report-jobs/noida/2025-27/JN25PG001?refresh=false"
        actor = "pseudonymous-actor"
        headers = _signed_agent_headers("POST", url, actor)
        parsed = urlsplit(url)
        target = parsed.path + "?" + parsed.query
        canonical = "\n".join((
            "POST", target, actor, headers["X-MCP-Timestamp"], headers["X-MCP-Nonce"]
        ))
        expected = hmac.new(
            settings.agent_shared_secret.encode(), canonical.encode(), hashlib.sha256
        ).hexdigest()
        assert headers["X-MCP-Signature"] == f"v1={expected}"
        queued = _queued({"request_id": "1" * 36}, "noida", "2025-27", False)
        assert queued["queued"] is True
        assert queued["generated"] is False
    finally:
        settings.agent_shared_secret = old


if __name__ == "__main__":
    test_normalization_and_link_allowlist()
    test_redirect_allowlist()
    test_subject_is_pseudonymous()
    test_local_rate_fallback()
    test_host_and_security_headers()
    test_report_queue_contract_and_signature()
    print("6 passed, 0 failed")
