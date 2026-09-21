"""Per-principal create_report budget (plain asserts). The one money-spending tool must
be capped per user so — under the open-access model — no single account can drive
runaway LLM generation. Verifies the cap triggers, is per-principal, disables at <=0,
and keys on a hash (never the raw email).

Run:  ../moodle-agent/.venv/bin/python tests/test_report_budget.py
from the moodle-mcp directory.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

import tools.actions as actions  # noqa: E402
from config import settings  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


class Svc:
    def __init__(self, email):
        self.principal = {"email": email}


async def _n_allowed(svc, n):
    """How many of n consecutive calls are allowed before ToolError."""
    allowed = 0
    for _ in range(n):
        try:
            await actions._enforce_report_budget(svc)
            allowed += 1
        except ToolError:
            break
    return allowed


async def main():
    settings.redis_url = ""            # in-process limiter
    settings.rate_limit_max_keys = 16384

    print("\n[ cap triggers after the budget is spent ]")
    actions._report_limiter = None
    settings.create_report_limit = 3
    settings.create_report_window_seconds = 3600
    allowed = await _n_allowed(Svc("a@jaipuria.ac.in"), 6)
    check("first 3 allowed, then blocked", allowed == 3)

    print("\n[ budgets are per-principal ]")
    actions._report_limiter = None
    settings.create_report_limit = 2
    a = await _n_allowed(Svc("x@jaipuria.ac.in"), 5)     # exhausts x
    b = await _n_allowed(Svc("y@jaipuria.ac.in"), 2)     # y untouched
    check("principal x capped at 2", a == 2)
    check("principal y still gets its own 2", b == 2)

    print("\n[ limit <= 0 disables the cap ]")
    actions._report_limiter = None
    settings.create_report_limit = 0
    z = await _n_allowed(Svc("z@jaipuria.ac.in"), 25)
    check("no cap when limit <= 0", z == 25)

    print("\n[ budget key is a hash, never the raw email ]")
    key = actions._budget_key(Svc("secret.person@jaipuria.ac.in"))
    check("key is a 24-char hash", len(key) == 24 and "secret" not in key)
    check("same identity → same key (case-insensitive)",
          actions._budget_key(Svc("A@Jaipuria.AC.in")) == actions._budget_key(Svc("a@jaipuria.ac.in")))


asyncio.run(main())
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
