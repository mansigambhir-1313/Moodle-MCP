"""Compatibility layer that makes the OAuth flow robust for EVERY connecting host.

Two real-world failure modes were observed with Claude.ai's connector (and would hit
any faculty member adding the server):

1. "No MCP server was found at the provided URL" — the user enters the bare domain
   without the /mcp path. OAuth completes, then the host POSTs to "/" and gets 404.
   Fix: PathAliases rewrites "/" -> "/mcp" (and the root discovery document to its
   path-scoped variant) so both URL forms work.

2. "invalid_grant / mcp_token_exchange_failed" — Claude.ai's backend registers
   SEVERAL dynamic clients concurrently (one per node), authorizes with one and
   redeems the code as another. FastMCP binds each code to the registering client
   and rejects the mismatch. Fix: TolerantGoogleProvider accepts the exchange from
   a different *registered* client as long as the code carries a PKCE challenge —
   the MCP token handler still verifies code_verifier, redirect_uri, expiry, and
   one-time use after this hook, so the cryptographic binding (RFC 7636) that
   actually protects the code is fully retained. Codes without PKCE keep the
   strict client check.

Pinned to fastmcp==2.14.7 (requirements.txt): the tolerant override mirrors that
version's storage internals.
"""
import logging
import time

from fastmcp.server.auth.providers.google import GoogleProvider
from mcp.server.auth.provider import AuthorizationCode
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

log = logging.getLogger("moodle-mcp.oauth_compat")

# Exact-path rewrites applied before routing. Kept deliberately tiny and explicit.
PATH_ALIASES = {
    # bare-domain connector URL -> the real MCP endpoint
    "/": "/mcp",
    # RFC 9728 resource-metadata for the bare URL -> the /mcp-scoped document
    "/.well-known/oauth-protected-resource": "/.well-known/oauth-protected-resource/mcp",
    # RFC 8414 path-insertion variant some clients probe for a /mcp resource
    "/.well-known/oauth-authorization-server/mcp": "/.well-known/oauth-authorization-server",
}


class PathAliases:
    """ASGI wrapper: rewrite a handful of exact paths so both the bare server URL
    and the /mcp-suffixed URL behave identically. No other request is touched."""

    def __init__(self, app, aliases: dict | None = None):
        self.app = app
        self.aliases = dict(aliases or PATH_ALIASES)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            target = self.aliases.get(scope.get("path", ""))
            if target:
                scope = dict(scope)
                scope["path"] = target
                scope["raw_path"] = target.encode()
        return await self.app(scope, receive, send)


class TolerantGoogleProvider(GoogleProvider):
    """GoogleProvider that tolerates Claude.ai's multi-client DCR race at /token.

    The stock provider refuses to load an authorization code when the exchanging
    client_id differs from the authorizing client_id. Claude.ai's distributed
    backend routinely trips this (it registers a client per node). For PKCE-bound
    codes we return the code anyway — the framework still enforces the verifier,
    redirect_uri, expiry and single use — and just log the mismatch."""

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        code_model = await self._code_store.get(key=authorization_code)
        if not code_model:
            log.debug("authorization code not found")
            return None
        if time.time() > code_model.expires_at:
            log.debug("authorization code expired")
            await self._code_store.delete(key=authorization_code)
            return None
        if code_model.client_id != client.client_id:
            if not code_model.code_challenge:
                # No PKCE -> the client binding is the only protection; keep it.
                log.warning("code client mismatch without PKCE — rejecting")
                return None
            log.info(
                "tolerating client mismatch at /token (authorized=%s exchanging=%s); "
                "PKCE still enforced",
                code_model.client_id, client.client_id,
            )
        if client.client_id is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            client_id=client.client_id,
            redirect_uri=AnyUrl(url=code_model.redirect_uri),
            redirect_uri_provided_explicitly=True,
            scopes=code_model.scopes,
            expires_at=code_model.expires_at,
            code_challenge=code_model.code_challenge or "",
        )
