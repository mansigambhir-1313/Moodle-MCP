"""Security-hardening verification (plain asserts; this repo has no pytest infra).

Run:  ../moodle-agent/.venv/bin/python tests/test_hardening.py
from the moodle-mcp directory (so local modules import).
"""
import os
import sys

# Import local modules (run from repo root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Minimal config so `import config` succeeds without a real .env.
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


# --------------------------------------------------------------------------
# Fake supabase client that records whether the DB was actually queried.
# --------------------------------------------------------------------------
class _Chain:
    def __init__(self, hits, rows):
        self._hits = hits
        self._rows = rows
        self.not_ = self

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def is_(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        self._hits.append(1)  # DB was touched

        class _R:
            data = self._rows
        return _R()


class FakeClient:
    def __init__(self, rows=None):
        self.db_hits = []
        self._rows = rows or []

    def table(self, name):
        return _Chain(self.db_hits, self._rows)


def phase1_latest_run_scope():
    print("PHASE 1 — latest_run is scope-aware")
    from supabase_client import MoodleService

    # jaipur-scoped caller
    fc = FakeClient(rows=[{"run_id": "RID", "finished_at": "t"}])
    svc = MoodleService(fc, {"name": "Jaipur", "campuses": ["jaipur"]})

    # out-of-scope campus -> None, and NO db query issued
    r = svc.latest_run("noida", "2024-26")
    check("out-of-scope campus returns None", r is None)
    check("out-of-scope short-circuits before any DB query", len(fc.db_hits) == 0)

    # in-scope campus -> proceeds to query and returns the run id
    r2 = svc.latest_run("jaipur", "2024-26")
    check("in-scope campus resolves the run id", r2 == "RID")
    check("in-scope campus did hit the DB", len(fc.db_hits) >= 1)

    # admin (campuses=None) -> any campus allowed
    fc2 = FakeClient(rows=[{"run_id": "RID2", "finished_at": "t"}])
    admin = MoodleService(fc2, {"name": "admin", "campuses": None})
    check("admin resolves any campus", admin.latest_run("noida", "2024-26") == "RID2")


def _make_jwt(role):
    import base64
    import json as _json

    def b64(d):
        return base64.urlsafe_b64encode(_json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64({'role': role, 'iss': 'supabase'})}.sig"


def phase2_key_role_detection():
    print("\nPHASE 2 — service_role (god-key) detection")
    from config import key_role

    check("detects service_role key", key_role(_make_jwt("service_role")) == "service_role")
    check("detects reporting_readonly key",
          key_role(_make_jwt("reporting_readonly")) == "reporting_readonly")
    check("opaque/non-JWT key -> None", key_role("not-a-jwt") is None)
    check("empty key -> None", key_role("") is None)


def phase3_transport_guard():
    print("\nPHASE 3 — transport guard (body cap / IP limit / auth / health)")
    import asyncio

    from security import TransportGuard

    app_calls = []

    async def app(scope, receive, send):
        app_calls.append(1)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def drive(guard, scope):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(m):
            sent.append(m)

        await guard(scope, receive, send)
        return next((m["status"] for m in sent if m["type"] == "http.response.start"), None)

    def mcp_scope(headers=None, ip="1.2.3.4"):
        return {"type": "http", "path": "/mcp", "method": "POST",
                "headers": headers or [], "client": (ip, 1234)}

    # 1. body cap -> 413, app never reached
    app_calls.clear()
    g = TransportGuard(app, max_body=1024, ip_rate_limit=240)
    st = asyncio.run(drive(g, mcp_scope(headers=[(b"content-length", b"999999")])))
    check("oversized body -> 413", st == 413)
    check("oversized body never reaches the app", not app_calls)

    # 2. per-IP limit -> first request passes IP (401 no-token), second 429
    g2 = TransportGuard(app, max_body=262144, ip_rate_limit=1)
    st1 = asyncio.run(drive(g2, mcp_scope(ip="9.9.9.9")))
    st2 = asyncio.run(drive(g2, mcp_scope(ip="9.9.9.9")))
    check("first request from IP passes the limiter (401, no token)", st1 == 401)
    check("second request from same IP -> 429", st2 == 429)

    # 3. /health stays open (no auth, passthrough)
    app_calls.clear()
    g3 = TransportGuard(app)
    sth = asyncio.run(drive(g3, {"type": "http", "path": "/health", "method": "GET",
                                 "headers": [], "client": ("1.2.3.4", 1)}))
    check("/health passes through to the app (200)", sth == 200 and app_calls)

    # 4. tokenless /mcp -> 401
    g4 = TransportGuard(app, ip_rate_limit=240)
    st401 = asyncio.run(drive(g4, mcp_scope(ip="2.2.2.2")))
    check("tokenless /mcp -> 401", st401 == 401)


def phase4_credential_hygiene():
    print("\nPHASE 4 — credential hygiene (expiry / strength / expires-format)")
    import importlib

    import config as cfgmod
    import security
    from config import settings as cfg
    from security import resolve_principal, token_expired

    check("past date -> expired", token_expired("2000-01-01") is True)
    check("future date -> not expired", token_expired("2999-12-31") is False)
    check("no expiry -> not expired", token_expired(None) is False)
    check("malformed expiry -> not expired (fail-open)", token_expired("nonsense") is False)

    # resolve_principal honours expiry (inject a known token map at the cache)
    security._token_cache["sig"] = (cfg.mcp_tokens_raw, cfg.mcp_admin_token)
    security._token_cache["map"] = {
        "tok-valid-000000000000000000": {"name": "A", "campuses": ["jaipur"]},
        "tok-expired-0000000000000000": {"name": "B", "campuses": ["jaipur"], "expires": "2000-01-01"},
        "tok-future-000000000000000000": {"name": "C", "campuses": ["jaipur"], "expires": "2999-12-31"},
    }
    check("valid token resolves", resolve_principal("tok-valid-000000000000000000") is not None)
    check("expired token rejected (revocable by date)",
          resolve_principal("tok-expired-0000000000000000") is None)
    check("future-dated token resolves", resolve_principal("tok-future-000000000000000000") is not None)

    def reload_with(**env):
        for k, v in env.items():
            os.environ[k] = v
        importlib.reload(cfgmod)

    def raises(fn):
        try:
            fn()
            return False
        except RuntimeError:
            return True

    reload_with(SUPABASE_URL="https://x.supabase.co", SUPABASE_SERVICE_ROLE_KEY="k",
                MCP_ADMIN_TOKEN="short", ALLOW_WEAK_TOKENS="false")
    check("weak admin token rejected at boot", raises(cfgmod.validate_config))

    reload_with(ALLOW_WEAK_TOKENS="true")
    check("ALLOW_WEAK_TOKENS lets a weak token boot", not raises(cfgmod.validate_config))

    os.environ.pop("MCP_ADMIN_TOKEN", None)
    os.environ.pop("ALLOW_WEAK_TOKENS", None)
    reload_with(MCP_TOKENS='{"averylongtoken-abcdefghijklmnop":'
                           '{"campuses":["jaipur"],"expires":"not-a-date"}}')
    check("bad expires format rejected at boot", raises(cfgmod.validate_config))
    os.environ.pop("MCP_TOKENS", None)
    importlib.reload(cfgmod)


def phase5_oauth_signin():
    print("\nPHASE 5 — Google OAuth sign-in (domain gate / grants / boot validation)")
    import importlib

    import config as cfgmod

    def reload_with(**env):
        for k, v in env.items():
            os.environ[k] = v
        importlib.reload(cfgmod)

    def raises(fn):
        try:
            fn()
            return False
        except RuntimeError:
            return True

    reload_with(SUPABASE_URL="https://x.supabase.co", SUPABASE_SERVICE_ROLE_KEY="k",
                GOOGLE_OAUTH_CLIENT_ID="id.apps.googleusercontent.com",
                GOOGLE_OAUTH_CLIENT_SECRET="GOCSPX-x",
                MCP_SERVER_BASE_URL="https://mcp.example.com",
                OAUTH_JWT_SIGNING_KEY="k" * 32)
    import security
    importlib.reload(security)
    from security import principal_from_claims

    p = principal_from_claims({"email": "Prof@Jaipuria.ac.in", "name": "Prof"})
    check("jaipuria.ac.in email accepted (case-insensitive)",
          p is not None and p["email"] == "prof@jaipuria.ac.in")
    check("default grant is all campuses", p is not None and p["campuses"] is None)
    check("outside domain rejected", principal_from_claims({"email": "x@gmail.com"}) is None)
    check("missing email rejected", principal_from_claims({"name": "X"}) is None)
    check("unverified email rejected (userinfo v2 spelling)",
          principal_from_claims({"email": "p@jaipuria.ac.in",
                                 "google_user_data": {"verified_email": False}}) is None)

    reload_with(MCP_FACULTY='{"dean@jaipuria.ac.in": {"name": "Dean", "campuses": ["jaipur"]}}',
                OAUTH_DEFAULT_CAMPUSES="none")
    p = principal_from_claims({"email": "dean@jaipuria.ac.in"})
    check("MCP_FACULTY override narrows campuses",
          p is not None and p["campuses"] == ["jaipur"])
    check("unlisted email denied when OAUTH_DEFAULT_CAMPUSES=none",
          principal_from_claims({"email": "other@jaipuria.ac.in"}) is None)

    reload_with(OAUTH_DEFAULT_CAMPUSES='["noida"]', MCP_FACULTY="")
    p = principal_from_claims({"email": "other@jaipuria.ac.in"})
    check("JSON-list default grant applies", p is not None and p["campuses"] == ["noida"])

    # Boot validation: half-configured OAuth and a non-https base URL must fail closed.
    reload_with(OAUTH_DEFAULT_CAMPUSES="all", GOOGLE_OAUTH_CLIENT_SECRET="")
    check("half-configured OAuth rejected at boot", raises(cfgmod.validate_config))
    reload_with(GOOGLE_OAUTH_CLIENT_SECRET="GOCSPX-x", MCP_SERVER_BASE_URL="")
    check("OAuth without https base_url rejected at boot", raises(cfgmod.validate_config))
    reload_with(MCP_SERVER_BASE_URL="https://mcp.example.com", OAUTH_ALLOWED_DOMAINS=" ")
    check("empty allowed-domains rejected at boot", raises(cfgmod.validate_config))

    for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "MCP_SERVER_BASE_URL",
              "OAUTH_ALLOWED_DOMAINS", "OAUTH_DEFAULT_CAMPUSES", "MCP_FACULTY",
              "OAUTH_JWT_SIGNING_KEY"):
        os.environ.pop(k, None)
    importlib.reload(cfgmod)
    importlib.reload(security)


def phase6_oauth_compat():
    print("\nPHASE 6 — OAuth compatibility (URL aliases / DCR-race tolerance)")
    import asyncio
    import time as _time

    from oauth_compat import PATH_ALIASES, PathAliases, TolerantGoogleProvider

    # --- PathAliases: exact rewrites only, everything else untouched ----------
    seen = {}

    async def inner(scope, receive, send):
        seen["path"] = scope.get("path")

    aliased = PathAliases(inner)

    def run(path, typ="http"):
        seen.clear()
        asyncio.run(aliased({"type": typ, "path": path}, None, None))
        return seen.get("path")

    check("bare / rewritten to /mcp", run("/") == "/mcp")
    check("root protected-resource doc rewritten",
          run("/.well-known/oauth-protected-resource")
          == "/.well-known/oauth-protected-resource/mcp")
    check("path-inserted auth-server doc rewritten",
          run("/.well-known/oauth-authorization-server/mcp")
          == "/.well-known/oauth-authorization-server")
    check("/mcp untouched", run("/mcp") == "/mcp")
    check("/health untouched", run("/health") == "/health")
    check("non-http scope untouched", run("/", typ="lifespan") == "/")

    # --- TolerantGoogleProvider: client mismatch tolerated only with PKCE -----
    provider = TolerantGoogleProvider(
        client_id="x.apps.googleusercontent.com", client_secret="GOCSPX-x",
        base_url="https://mcp.example.com",
        required_scopes=["openid"],
    )

    class FakeStore:
        def __init__(self, model):
            self.model, self.deleted = model, False

        async def get(self, key):
            return self.model

        async def delete(self, key):
            self.deleted = True

    class CodeModel:
        client_id = "client-A"
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"
        scopes = ["openid"]
        expires_at = _time.time() + 300
        code_challenge = "challenge123"

    class ClientB:
        client_id = "client-B"

    class ClientA:
        client_id = "client-A"

    provider._code_store = FakeStore(CodeModel())
    same = asyncio.run(provider.load_authorization_code(ClientA(), "code1"))
    check("same-client exchange loads code",
          same is not None and same.code_challenge == "challenge123")
    cross = asyncio.run(provider.load_authorization_code(ClientB(), "code1"))
    check("cross-client exchange tolerated WITH PKCE (race fix)",
          cross is not None and cross.client_id == "client-B"
          and cross.code_challenge == "challenge123")

    class NoPkce(CodeModel):
        code_challenge = ""

    provider._code_store = FakeStore(NoPkce())
    check("cross-client exchange REJECTED without PKCE",
          asyncio.run(provider.load_authorization_code(ClientB(), "code1")) is None)

    class Expired(CodeModel):
        expires_at = _time.time() - 1

    store = FakeStore(Expired())
    provider._code_store = store
    check("expired code rejected and deleted",
          asyncio.run(provider.load_authorization_code(ClientA(), "code1")) is None
          and store.deleted)

    class EmptyStore(FakeStore):
        async def get(self, key):
            return None

    provider._code_store = EmptyStore(None)
    check("unknown code rejected",
          asyncio.run(provider.load_authorization_code(ClientA(), "nope")) is None)


if __name__ == "__main__":
    phase1_latest_run_scope()
    phase2_key_role_detection()
    phase3_transport_guard()
    phase4_credential_hygiene()
    phase5_oauth_signin()
    phase6_oauth_compat()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
