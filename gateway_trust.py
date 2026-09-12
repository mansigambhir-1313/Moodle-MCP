"""Downstream bypass resistance (AIA-1013 #4).

When GATEWAY_ENFORCED, this server accepts /mcp requests ONLY if they carry the
gateway's shared secret — i.e. only traffic that came through the gateway. OAuth is
unchanged (the gateway forwards the caller's bearer, validated by the normal auth
layer); this is an ADDITIONAL gate, not a replacement. Health and OAuth discovery/
token/register paths stay open so the handshake still works.

Defaults OFF: inert until the gateway fully fronts this server, at which point flip
GATEWAY_ENFORCED=true and set GATEWAY_SHARED_SECRET (matching the gateway).
"""
import hmac
import json

_SECRET_HEADER = b"x-mcp-gateway-secret"


def _presented_secret(scope) -> str | None:
    for k, v in scope.get("headers", []):
        if k.lower() == _SECRET_HEADER:
            try:
                return v.decode("latin-1")
            except Exception:  # noqa: BLE001
                return None
    return None


def secret_ok(scope, accepted_secrets) -> bool:
    """Constant-time check that the request carries an accepted gateway secret.
    Fail-CLOSED: no configured secret, or no/blank header, => False."""
    accepted = [s for s in (accepted_secrets or []) if s]
    if not accepted:
        return False
    presented = _presented_secret(scope)
    if not presented:
        return False
    return any(hmac.compare_digest(presented, s) for s in accepted)


class GatewayEnforce:
    """ASGI wrapper. When `enforced`, an HTTP request whose path starts with one of
    `guarded_prefixes` must carry a valid gateway secret, else 403. Everything else
    (and all traffic when not enforced) passes straight through."""

    def __init__(self, app, *, enforced: bool, accepted_secrets, guarded_prefixes=("/mcp",)):
        self.app = app
        self.enforced = bool(enforced)
        self.accepted = list(accepted_secrets or [])
        self.guarded = tuple(guarded_prefixes)

    async def __call__(self, scope, receive, send):
        if (self.enforced and scope.get("type") == "http"
                and any(scope.get("path", "").startswith(p) for p in self.guarded)
                and not secret_ok(scope, self.accepted)):
            body = json.dumps({
                "error": "gateway_required",
                "error_description": "This MCP is reachable only through the gateway.",
            }).encode()
            await send({"type": "http.response.start", "status": 403, "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
