"""Security layer — central auth resolution, rate limiting, audit logging, and a single
error boundary for every tool. Kept deliberately dependency-free (stdlib only) and bounded
(no unbounded dicts) so it is safe to run in a long-lived process.

Wiring (see server.py):
  * TransportGuard (ASGI)      — every /mcp request needs a valid bearer -> real 401; /health open.
  * GuardMiddleware (FastMCP)  — per tool-call: rate limit + audit + catch-all error boundary.
  * resolve_principal(token)   — cached, constant-time token -> principal.
"""
import hashlib
import hmac
import json
import logging
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone

log = logging.getLogger("moodle-mcp.security")

# Generic, internal-detail-free messages returned to the caller.
MSG_DENIED = "Access denied for your token."
MSG_RATE = "Rate limit exceeded — please slow down and retry shortly."
MSG_ERROR = "This query could not be completed right now. Please retry."


def quiet_noisy_loggers() -> None:
    """Cap third-party HTTP client loggers at WARNING. At INFO, httpx logs every
    request URL — including Google's tokeninfo endpoint, whose query string carries
    the caller's LIVE access token — straight into the platform logs. Anyone with
    log access could replay that token for its remaining lifetime. Our own
    "moodle-mcp*" loggers are unaffected and stay at INFO."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


# --- token resolution: cached map + constant-time compare --------------------
_token_cache: dict = {"sig": None, "map": {}}


def _token_map() -> dict:
    """settings.tokens(), recomputed only when the raw config changes (avoids json.loads/request)."""
    from config import settings
    sig = (settings.mcp_tokens_raw, settings.mcp_admin_token)
    if _token_cache["sig"] != sig:
        _token_cache["map"] = settings.tokens()
        _token_cache["sig"] = sig
    return _token_cache["map"]


def token_expired(exp) -> bool:
    """True if an ISO date/datetime `expires` value is in the past. A date-only
    value ('2026-12-31') expires at the END of that UTC day. Malformed values are
    treated as NOT expired at runtime (validate_config rejects bad formats at boot,
    so a runtime parse error shouldn't lock a valid token out and cause an outage)."""
    if not exp:
        return False
    try:
        s = str(exp).strip()
        if len(s) == 10:  # date only
            dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc) + timedelta(days=1)
        else:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= dt
    except Exception:  # noqa: BLE001
        return False


def resolve_principal(token: str):
    """token -> principal dict, or None. Constant-time over the known tokens (no early-exit
    timing signal). Returns a copy so callers can't mutate the shared config. An expired
    token (optional per-token `expires`) resolves to None, so it can be revoked by date
    without a redeploy."""
    if not token:
        return None
    matched = None
    for known, principal in _token_map().items():
        if hmac.compare_digest(token, known):
            matched = principal
    if matched is None:
        return None
    if token_expired(matched.get("expires")):
        return None
    return dict(matched)


def bearer_of(headers: dict) -> str:
    """Extract the bearer token from a header mapping (case-insensitive)."""
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


# --- OAuth (Google sign-in) principal resolution -----------------------------
def principal_from_claims(claims: dict):
    """Verified Google claims -> campus-scoped principal, or None (fail-closed).
    Gate 1: email present and verified. Gate 2: domain in OAUTH_ALLOWED_DOMAINS
    (jaipuria.ac.in). Grant order (first match wins):
      1. MCP_FACULTY env override — deploy-time break-glass, admin-controlled;
      2. student-roster HARD DENY — 3,144 students share the Google domain, and
         data (the roster) must never be able to lock out the env-listed admin,
         which is why the env override is checked first;
      3. mcp_faculty DB registry row (scales to ~500 faculty, no redeploys);
      4. OAUTH_DEFAULT_CAMPUSES ('none' in production -> deny)."""
    from config import settings
    email = str(claims.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return None
    verified = claims.get("email_verified")
    if verified is None:  # Google userinfo v2 spells it verified_email
        verified = (claims.get("google_user_data") or {}).get("verified_email")
    if verified is False:
        return None
    domain = email.rsplit("@", 1)[1]
    if domain not in settings.oauth_allowed_domains():
        log.warning("oauth sign-in rejected: domain %r not allowed", domain)
        return None
    override = settings.faculty().get(email)
    if override is not None:
        return {"name": override.get("name") or claims.get("name") or email,
                "email": email, "campuses": override.get("campuses")}
    import faculty as registry
    if registry.is_student(email):
        return None  # roster accounts never get access (logged in the registry)
    grant = registry.faculty_grant(email)
    if grant is not None:
        return {"name": grant.get("name") or claims.get("name") or email,
                "email": email, "campuses": grant["campuses"]}
    default = settings.oauth_default_campuses()
    if default == "deny":
        log.warning("oauth sign-in rejected: %s has no mcp_faculty grant "
                    "(OAUTH_DEFAULT_CAMPUSES=none)", email)
        return None
    return {"name": claims.get("name") or email, "email": email, "campuses": default}


def resolve_oauth_principal():
    """Principal behind the FastMCP-issued OAuth token on the current request, or
    None when there is no (valid) OAuth token in context. Never raises."""
    try:
        from fastmcp.server.dependencies import get_access_token
        token = get_access_token()
    except Exception:  # noqa: BLE001 - no auth context / no token
        return None
    if token is None:
        return None
    claims = getattr(token, "claims", None) or {}
    return principal_from_claims(claims)


# --- bounded sliding-window rate limiter -------------------------------------
class RateLimiter:
    """Per-key sliding window. Bounded in memory (LRU-evicts idle keys) — OOM-safe."""

    def __init__(self, limit: int, window: float, maxkeys: int = 4096):
        self.limit = max(1, int(limit))
        self.window = float(window)
        self.maxkeys = maxkeys
        self._hits: "OrderedDict[str, deque]" = OrderedDict()

    def allow(self, key: str) -> tuple[bool, float]:
        """(allowed, retry_after_seconds). Records the hit when allowed."""
        now = time.monotonic()
        dq = self._hits.get(key)
        if dq is None:
            dq = deque()
            self._hits[key] = dq
        self._hits.move_to_end(key)
        while dq and now - dq[0] > self.window:
            dq.popleft()
        if len(dq) >= self.limit:
            return False, max(0.0, self.window - (now - dq[0]))
        dq.append(now)
        while len(self._hits) > self.maxkeys:
            self._hits.popitem(last=False)
        return True, 0.0


# --- audit ------------------------------------------------------------------
def audit(tool: str, principal, *, ok: bool, scope=None, note: str = "") -> None:
    """One structured line per authenticated tool call. NEVER logs tokens, student ids, names,
    or any PII value — only who (principal name), which tool, campus scope, and outcome."""
    who = principal.get("name", "?") if isinstance(principal, dict) else "anon"
    log.info("audit tool=%s who=%s ok=%s scope=%s%s",
             tool, who, "1" if ok else "0", scope or "-", f" note={note}" if note else "")


# --- ASGI transport gate ----------------------------------------------------
async def _send_json(send, status: int, payload: dict, extra: dict | None = None) -> None:
    body = json.dumps(payload).encode()
    headers = [(b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode())]
    if extra:
        headers += list(extra.items())
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def client_ip(scope, headers: dict) -> str:
    """Best-effort client IP for pre-auth rate limiting. Prefers the first hop of
    X-Forwarded-For (Render/most PaaS set it), falls back to the socket peer."""
    xff = headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    client = scope.get("client")
    return client[0] if client else "unknown"


class TransportGuard:
    """First line of defense on every /mcp request, before JSON-RPC:
      * cap the request body (declared Content-Length) -> 413, so an unbounded
        POST can't pressure memory;
      * per-IP rate limit -> 429, to blunt unauthenticated floods / token guessing;
      * require a valid bearer -> real 401, so tool enumeration is impossible.
    /health stays open; OPTIONS (credential-free CORS preflight) passes through;
    non-http scopes (lifespan) pass untouched. All guards fail open on internal
    error so the transport itself never wedges a legitimate request."""

    def __init__(self, app, open_paths=("/health",), max_body: int = 262144,
                 ip_rate_limit: int = 240, ip_window: float = 60.0,
                 check_bearer: bool = True):
        # check_bearer=False (OAuth mode): FastMCP's auth layer owns token
        # validation and the 401 + WWW-Authenticate resource-metadata handshake
        # the MCP OAuth discovery flow depends on, and the OAuth endpoints
        # (/.well-known/*, /register, /authorize, /token, /auth/callback) must be
        # reachable pre-auth. Body cap + per-IP limiting stay on either way.
        self.app = app
        self.open_paths = set(open_paths)
        self.max_body = max_body
        self.ip_limiter = RateLimiter(ip_rate_limit, ip_window) if ip_rate_limit else None
        self.check_bearer = check_bearer

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path in self.open_paths or scope.get("method") == "OPTIONS":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}

        # 1. body-size cap (declared Content-Length)
        cl = headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > self.max_body:
                    return await _send_json(send, 413, {"error": "request_too_large"})
            except ValueError:
                pass

        # 2. per-IP pre-auth rate limit (fail-open)
        if self.ip_limiter is not None:
            try:
                allowed, _retry = self.ip_limiter.allow(client_ip(scope, headers))
                if not allowed:
                    return await _send_json(send, 429, {"error": "rate_limited"})
            except Exception:  # noqa: BLE001
                pass

        # 3. auth (static-token mode only; OAuth mode delegates to FastMCP auth)
        if not self.check_bearer:
            return await self.app(scope, receive, send)
        token = bearer_of(headers)
        if resolve_principal(token) is None:
            return await _send_json(send, 401, {"error": "unauthorized"},
                                    extra={b"www-authenticate": b'Bearer realm="moodle-mcp"'})
        return await self.app(scope, receive, send)


# --- FastMCP per-call middleware: rate limit + audit + error boundary --------
def build_middleware(rate_limit: int, window: float):
    from fastmcp.exceptions import ToolError
    from fastmcp.server.dependencies import get_http_headers
    from fastmcp.server.middleware import Middleware
    from pydantic import ValidationError

    limiter = RateLimiter(rate_limit, window)

    def _principal():
        try:
            return resolve_oauth_principal() \
                or resolve_principal(bearer_of(get_http_headers() or {}))
        except Exception:  # noqa: BLE001 - never let auth-introspection break a call
            return None

    def _rate_key():
        # Key the limiter by the TOKEN (hashed), not the principal name — two
        # tokens that happen to share a name must not share a rate budget. The
        # hash keeps raw tokens out of the in-memory limiter map.
        try:
            tok = bearer_of(get_http_headers() or {})
            return hashlib.sha256(tok.encode()).hexdigest()[:16] if tok else "anon"
        except Exception:  # noqa: BLE001
            return "anon"

    def _scope(context):
        try:
            args = getattr(context.message, "arguments", None) or {}
            p = args.get("params") if isinstance(args, dict) else None
            return (p or {}).get("campus") if isinstance(p, dict) else None
        except Exception:  # noqa: BLE001
            return None

    class GuardMiddleware(Middleware):
        async def on_call_tool(self, context, call_next):
            name = getattr(context.message, "name", "?")
            principal = _principal()
            ok, _retry = limiter.allow(_rate_key())
            if not ok:
                audit(name, principal, ok=False, note="rate_limited")
                raise ToolError(MSG_RATE)
            try:
                result = await call_next(context)
                audit(name, principal, ok=True, scope=_scope(context))
                return result
            except ToolError:
                raise  # already a clean, caller-safe message
            except ValidationError as e:
                # Caller sent bad/missing parameters. Tell them WHICH — this is
                # their own input, not an internal detail — so an agent can fix
                # the call instead of uselessly retrying a "server error".
                first = (e.errors() or [{}])[0]
                loc = ".".join(str(x) for x in first.get("loc", ())) or "params"
                audit(name, principal, ok=False, note="bad_params")
                raise ToolError(f"Invalid parameters — {loc}: "
                                f"{first.get('msg', 'validation failed')}")
            except PermissionError:
                audit(name, principal, ok=False, note="unauthorized")
                raise ToolError(MSG_DENIED)
            except Exception:  # noqa: BLE001 - the point is to never leak internals
                log.exception("tool %s failed", name)
                audit(name, principal, ok=False, note="error")
                raise ToolError(MSG_ERROR)

    return GuardMiddleware()
