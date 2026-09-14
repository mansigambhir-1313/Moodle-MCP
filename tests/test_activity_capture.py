"""Activity-capture gating verification (plain asserts; no pytest infra here).

Proves the MCP_CAPTURE_* flags widen the audit `metadata` blob exactly as
intended, and that with every flag OFF (the default) capture stays byte-for-byte
the historic privacy-safe shape — enabling audit alone must never start logging
identity, arguments, results or IP.

Run:  ../moodle-agent/.venv/bin/python tests/test_activity_capture.py
from the moodle-mcp directory.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

import audit_store  # noqa: E402
from config import settings  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


PRINCIPAL = {"email": "faculty@jaipuria.ac.in", "name": "A Faculty",
             "campuses": None, "sub": "google-123"}
ARGS = {"params": {"student_id": "Aashna Gupta", "campus": "Jaipur"}}


class _Result:
    """Mimics a FastMCP tool result carrying structured content."""
    structured_content = {"student_id": "JN25MM002", "attendance_pct": 91,
                          "report_url": "https://reports.tryrehearsal.ai/x",
                          "from_cache": True}


def _set(**flags):
    for k, v in flags.items():
        setattr(settings, k, v)


# Save originals so the process-wide settings singleton is restored between cases.
_ORIG = {k: getattr(settings, k) for k in (
    "capture_identity", "capture_arguments", "capture_results", "capture_client_ip",
    "capture_args_max_bytes", "capture_result_max_bytes")}


# --------------------------------------------------------------------------
print("\n[ defaults OFF — privacy-safe shape preserved ]")
_set(capture_identity=False, capture_arguments=False, capture_results=False,
     capture_client_ip=False)
meta = audit_store.build_metadata(identity=PRINCIPAL, arguments=ARGS,
                                  result=_Result(), source_ip="10.1.2.3")
check("only server_version present", set(meta) == {"server_version"})
check("no identity leaked", "identity" not in meta)
check("no arguments leaked", "arguments" not in meta)
check("no result leaked", "result" not in meta)
check("no source_ip leaked", "source_ip" not in meta)


# --------------------------------------------------------------------------
print("\n[ all flags ON — full capture ]")
_set(capture_identity=True, capture_arguments=True, capture_results=True,
     capture_client_ip=True)
meta = audit_store.build_metadata(identity=PRINCIPAL, arguments=ARGS,
                                  result=_Result(), source_ip="10.1.2.3")
check("identity captured with real email", meta.get("identity", {}).get("email")
      == "faculty@jaipuria.ac.in")
check("identity keeps name + campuses", meta["identity"].get("name") == "A Faculty"
      and "campuses" in meta["identity"])
check("arguments captured verbatim (small)",
      meta.get("arguments", {}).get("params", {}).get("student_id") == "Aashna Gupta")
check("result summarised from structured_content",
      meta.get("result", {}).get("student_id") == "JN25MM002")
check("result keeps report_url + from_cache",
      meta["result"].get("report_url", "").endswith("/x")
      and meta["result"].get("from_cache") is True)
check("source_ip captured", meta.get("source_ip") == "10.1.2.3")


# --------------------------------------------------------------------------
print("\n[ selective — identity only, no payloads ]")
_set(capture_identity=True, capture_arguments=False, capture_results=False,
     capture_client_ip=False)
meta = audit_store.build_metadata(identity=PRINCIPAL, arguments=ARGS,
                                  result=_Result(), source_ip="10.1.2.3")
check("identity on, arguments/result/ip still off",
      "identity" in meta and not ({"arguments", "result", "source_ip"} & set(meta)))


# --------------------------------------------------------------------------
print("\n[ size caps — oversize argument is truncated, not dropped ]")
_set(capture_arguments=True, capture_args_max_bytes=64)
big = {"params": {"note": "x" * 5000}}
meta = audit_store.build_metadata(arguments=big)
check("oversize arguments flagged _truncated",
      meta.get("arguments", {}).get("_truncated") is True)
check("truncated blob records real byte size",
      meta["arguments"].get("_bytes", 0) > 4000)
check("truncated preview respects the cap",
      len(meta["arguments"].get("preview", "")) <= 64)


# --------------------------------------------------------------------------
print("\n[ robustness — unserialisable / odd results never raise ]")
_set(capture_results=True, capture_result_max_bytes=8192)


class _Weird:
    def __repr__(self):
        return "weird-object"


meta = audit_store.build_metadata(result=_Weird())
check("plain object result summarised to a string form",
      isinstance(meta.get("result"), str) or isinstance(meta.get("result"), dict))
check("build_metadata never raised on odd input", True)


# restore
_set(**_ORIG)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
