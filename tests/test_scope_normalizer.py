"""ScopeNormalizer: short OIDC scope names ('email'/'profile') are rewritten to the
full Google scope URLs on the OAuth endpoints, so a client sending the short names is
accepted at DCR/authorize instead of rejected. Full URLs and non-OAuth paths are
untouched. No network; drives the ASGI middleware directly.
"""
import asyncio
import json
import os
import sys
from urllib.parse import parse_qsl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

from oauth_compat import ScopeNormalizer  # noqa: E402

EMAIL = "https://www.googleapis.com/auth/userinfo.email"
PROFILE = "https://www.googleapis.com/auth/userinfo.profile"
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    ok = bool(cond)
    PASS += ok
    FAIL += (not ok)
    print(f"  {'✓' if ok else '✗'} {name}{('  → ' + str(detail)) if detail else ''}")


class _Capture:
    """Downstream ASGI app that records the scope + body it actually received."""
    def __init__(self):
        self.query = None
        self.body = None
        self.headers = None

    async def __call__(self, scope, receive, send):
        self.query = scope.get("query_string", b"").decode()
        self.headers = scope.get("headers", [])
        if scope.get("method") == "POST":
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if not msg.get("more_body"):
                    break
            self.body = body


def _run(path, method, *, query=b"", body=b"", content_type=b"application/json"):
    cap = _Capture()
    mw = ScopeNormalizer(cap)
    headers = [(b"content-type", content_type),
               (b"content-length", str(len(body)).encode())]
    scope = {"type": "http", "path": path, "method": method,
             "query_string": query, "headers": headers}
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(msg):
        pass

    asyncio.new_event_loop().run_until_complete(mw(scope, receive, send))
    return cap


def main():
    print("ScopeNormalizer — short OIDC scopes accepted on OAuth endpoints")

    # 1. GET /authorize query: email profile -> full URLs
    cap = _run("/authorize", "GET", query=b"response_type=code&scope=openid+email+profile&state=x")
    q = dict(parse_qsl(cap.query))
    check("/authorize query email->full", EMAIL in q["scope"] and PROFILE in q["scope"], q["scope"])
    check("/authorize keeps openid + other params", "openid" in q["scope"] and q.get("state") == "x")

    # 2. POST /register JSON: scope rewritten in body
    reg = json.dumps({"client_name": "c", "scope": "openid email profile"}).encode()
    cap = _run("/register", "POST", body=reg, content_type=b"application/json")
    data = json.loads(cap.body)
    check("/register JSON email->full", EMAIL in data["scope"] and PROFILE in data["scope"], data["scope"])
    hdrs = {k.lower(): v for k, v in cap.headers}
    check("/register content-length corrected",
          hdrs.get(b"content-length") == str(len(cap.body)).encode(),
          hdrs.get(b"content-length"))

    # 3. POST /token form: scope rewritten
    cap = _run("/token", "POST", body=b"grant_type=authorization_code&scope=email",
               content_type=b"application/x-www-form-urlencoded")
    tf = dict(parse_qsl(cap.body.decode()))
    check("/token form email->full", tf["scope"] == EMAIL, tf["scope"])

    # 4. Full URLs pass through byte-identical (no needless rewrite)
    full = f"openid {EMAIL} {PROFILE}"
    cap = _run("/authorize", "GET", query=f"scope={full}".encode())
    check("full scopes untouched", dict(parse_qsl(cap.query))["scope"] == full)

    # 5. Non-OAuth path is never touched
    cap = _run("/mcp", "POST", body=b'{"scope":"email"}', content_type=b"application/json")
    check("/mcp body untouched", cap.body == b'{"scope":"email"}', cap.body)

    # 6. Parse/type errors still replay the original body downstream. The middleware
    # has consumed receive already, so this is what makes its fail-open promise real.
    cap = _run("/register", "POST", body=b'{not-json', content_type=b"application/json")
    check("malformed JSON replayed unchanged", cap.body == b'{not-json', cap.body)
    cap = _run("/register", "POST", body=b'[]', content_type=b"application/json")
    check("non-object JSON replayed unchanged", cap.body == b'[]', cap.body)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
