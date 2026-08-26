#!/usr/bin/env python3
"""Mint a least-privilege read-only JWT for the Moodle Reports MCP.

The token carries role=reporting_readonly (the SELECT-only role created by
sql/2026-08-26_reporting_readonly_role.sql), so PostgREST runs every query as a
role that physically cannot write — replacing the RLS-bypassing service_role key.

The JWT SECRET is read from the environment, never from argv, so it can't leak
into shell history or the process list. Only the finished token is printed to
stdout; all diagnostics go to stderr — so `... > key.txt` captures exactly the JWT.

Usage:

    SUPABASE_JWT_SECRET='<project JWT secret>' python3 scripts/mint_readonly_jwt.py

    # optional lifetime + project-ref claim:
    SUPABASE_JWT_SECRET='...' JWT_YEARS=5 PROJECT_REF=sadbfvfcmmxgtatfjfmc \
        python3 scripts/mint_readonly_jwt.py

Where to get the secret: Supabase dashboard -> Project Settings -> API ->
JWT Settings -> JWT Secret (the legacy HS256 shared secret that also signs your
anon/service keys).

Then set the printed token as SUPABASE_SERVICE_ROLE_KEY in Render and redeploy.
The server logs a warning while a full service_role key is still in use, and stops
warning once this reporting_readonly token is in place.

Stdlib only — no PyJWT required.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def mint(secret: str, *, years: int = 10, ref: str = "") -> str:
    """Return an HS256 JWT with role=reporting_readonly signed by `secret`."""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "role": "reporting_readonly",
        "iss": "supabase",
        "iat": now,
        "exp": now + years * 365 * 24 * 3600,
    }
    if ref:
        payload["ref"] = ref
    signing_input = (
        f"{_b64(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64(json.dumps(payload, separators=(',', ':')).encode())}"
    )
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(sig)}"


def main() -> int:
    secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    if not secret:
        sys.stderr.write(
            "error: set SUPABASE_JWT_SECRET in the environment (do NOT pass it as an "
            "argument). See the module docstring for where to find it.\n"
        )
        return 2
    try:
        years = int(os.environ.get("JWT_YEARS", "10"))
    except ValueError:
        sys.stderr.write("error: JWT_YEARS must be an integer.\n")
        return 2
    ref = os.environ.get("PROJECT_REF", "")

    print(mint(secret, years=years, ref=ref))  # stdout = the token only
    sys.stderr.write(
        f"minted reporting_readonly JWT (role=reporting_readonly, exp=+{years}y"
        f"{', ref=' + ref if ref else ''}).\n"
        "Set it as SUPABASE_SERVICE_ROLE_KEY in Render, then redeploy.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
