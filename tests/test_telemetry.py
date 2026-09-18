"""OTel telemetry gating (plain asserts). Verifies tracing is off unless configured,
the New Relic OTLP wiring is derived correctly, setup never raises, and the span
helpers are safe no-ops whether or not a provider/SDK is present.

Run:  ../moodle-agent/.venv/bin/python tests/test_telemetry.py
from the moodle-mcp directory.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

import security  # noqa: E402
import telemetry  # noqa: E402
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


_ORIG = {k: getattr(settings, k) for k in
         ("new_relic_license_key", "otel_headers_raw", "otel_endpoint")}


def _set(**kw):
    for k, v in kw.items():
        setattr(settings, k, v)


print("\n[ config gating ]")
_set(new_relic_license_key="", otel_headers_raw="")
check("disabled with no key/headers", settings.otel_enabled() is False)
check("setup_telemetry() no-ops to False when disabled", telemetry.setup_telemetry(settings) is False)

_set(new_relic_license_key="NRAK-testkey")
check("enabled once a license key is set", settings.otel_enabled() is True)
check("traces endpoint appends /v1/traces",
      settings.otel_traces_endpoint() == "https://otlp.eu01.nr-data.net/v1/traces")
check("NR ingest header derived from license key",
      settings.otel_headers() == {"api-key": "NRAK-testkey"})

_set(new_relic_license_key="", otel_headers_raw="api-key=abc,x-tenant=jaipuria")
check("explicit MCP_OTEL_HEADERS parsed",
      settings.otel_headers() == {"api-key": "abc", "x-tenant": "jaipuria"})

print("\n[ setup never raises; returns a bool ]")
_set(new_relic_license_key="NRAK-testkey", otel_headers_raw="")
res = telemetry.setup_telemetry(settings)  # True if SDK installed, False if not — never raises
check("setup_telemetry returns a bool (no exception)", isinstance(res, bool))

print("\n[ tool span helpers are safe no-ops ]")
span = security._start_tool_span("get_student")   # None, or a (non-recording) span
security._end_tool_span(span, "success", None, "noida")     # must not raise
security._end_tool_span(span, "failure", "unauthorized", None)
security._end_tool_span(None, "failure", "rate_limited", None)  # explicit None path
check("start/end tool span never raise", True)
check("get_tracer() returns something or None", telemetry.get_tracer() is not None or True)

print("\n[ generic auth span helpers are safe no-ops ]")
asp = telemetry.start_span("mcp.auth")
telemetry.end_span(asp, "success", None, {"mcp.auth.mode": "oauth"})   # must not raise
telemetry.end_span(telemetry.start_span("mcp.auth"), "failure", "unauthorized",
                   {"mcp.auth.mode": "static"})
telemetry.end_span(None, "failure", "auth_error", None)               # explicit None path
telemetry.end_span(None)                                              # no-arg None path
check("start_span/end_span never raise (incl. None)", True)

_set(**_ORIG)
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
