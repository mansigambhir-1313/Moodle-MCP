"""Regression: create_report with only a student_id must locate that student's OWN
campus/batch — not blindly pick the first graded scope.

The live bug: create_report(student_id="JN25MM002") returned
'not in run for indore/2025-27' because the code used scopes[0] (Indore) instead of
finding that JN25MM002 is a Noida student. Also verifies the trimester param is
threaded through to the agent call.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")
# report generation is gated on these three being set (settings.report_generation_enabled);
# set them BEFORE importing config so the gate is open for this test.
os.environ.setdefault("AGENT_API_BASE", "https://agent.example.com")
os.environ.setdefault("AGENT_ADMIN_USER", "u")
os.environ.setdefault("AGENT_ADMIN_PASS", "p")

import tools.common as common  # noqa: E402
from tools import actions  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    ok = bool(cond)
    PASS += ok
    FAIL += (not ok)
    print(f"  {'✓' if ok else '✗'} {name}{('  → ' + str(detail)) if detail else ''}")


class _Svc:
    def campus_scope(self, campus):
        return None  # unrestricted (all-grant caller); [] would mean "denied"

    def latest_run(self, campus, batch):
        return f"run_{campus}"  # a graded run exists for every located scope


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_locates_students_own_campus(monkeypatch=None):
    print("BUG #1 — student_id-only report locates the student's REAL campus, not scopes[0]")
    # graded scopes with Indore FIRST — the old code would have picked this and failed.
    common_scopes = [("indore", "2025-27", "run_i"),
                     ("noida", "2025-27", "run_n"),
                     ("lucknow", "2025-27", "run_l")]
    orig = {"graded_scopes": common.graded_scopes, "find_student": common.find_student,
            "gen": actions._agent_generate}
    calls = {}

    common.graded_scopes = lambda svc, campus=None, batch=None: list(common_scopes)
    common.find_student = lambda svc, sid: (
        {"student_id": sid, "student_name": "Aashna Gupta", "campus": "noida",
         "batch": "2025-27", "section_group": None} if sid == "JN25MM002" else None)

    async def fake_gen(campus, batch, student_id, refresh, trimester=None):
        calls["scope"] = (campus, batch)
        calls["trimester"] = trimester
        return 200, {"student_id": student_id, "name": "Aashna Gupta", "trimester": "3"}

    actions._agent_generate = fake_gen
    try:
        p = actions.CreateReportParams(student_id="JN25MM002")
        out = _run(actions._create_impl(_Svc(), p))
        check("agent was called for NOIDA, not Indore", calls.get("scope") == ("noida", "2025-27"),
              calls.get("scope"))
        check("result campus is noida", out.get("campus") == "noida", out.get("campus"))
        check("found is True", out.get("found") is True, out)

        # unknown student -> honest 'not found', never a wrong-campus attempt
        calls.clear()
        p2 = actions.CreateReportParams(student_id="ZZ99XX999")
        out2 = _run(actions._create_impl(_Svc(), p2))
        check("unknown student -> found:false, no agent call",
              out2.get("found") is False and "scope" not in calls, out2)

        # trimester param is threaded through to the agent
        calls.clear()
        p3 = actions.CreateReportParams(student_id="JN25MM002", trimester="2")
        _run(actions._create_impl(_Svc(), p3))
        check("explicit trimester forwarded to agent", calls.get("trimester") == "2",
              calls.get("trimester"))
    finally:
        common.graded_scopes = orig["graded_scopes"]
        common.find_student = orig["find_student"]
        actions._agent_generate = orig["gen"]


if __name__ == "__main__":
    test_locates_students_own_campus()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
