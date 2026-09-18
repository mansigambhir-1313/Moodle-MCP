"""OpenTelemetry tracing → New Relic (EU), gated on configuration.

Enabled only when NEW_RELIC_LICENSE_KEY (or an explicit MCP_OTEL_HEADERS override) is
set. When disabled — or when the OTel SDK isn't installed — setup is a no-op and the
server behaves exactly as before. Instrumentation elsewhere uses the OTel *API* only
(a no-op tracer when no provider is installed), so it is always safe to call.

Spans: one per tool call, named ``mcp.tool.<name>``, kind SERVER, carrying
``mcp.tool`` / ``mcp.outcome`` / ``mcp.error_code`` / ``mcp.campus_scope`` and an ERROR
status on failure. New Relic derives duration.ms, error, throughput and percentiles
from these — see monitoring/README.md for the alert NRQL.
"""
import logging

log = logging.getLogger("moodle-mcp.telemetry")

_INSTRUMENTATION = "moodle-mcp"


def setup_telemetry(settings) -> bool:
    """Install the OTel SDK + New Relic OTLP HTTP exporter. Returns True if tracing was
    enabled, False otherwise. Never raises — a telemetry problem must not stop boot."""
    if not settings.otel_enabled():
        log.info("OTel tracing disabled (no NEW_RELIC_LICENSE_KEY / MCP_OTEL_HEADERS)")
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("OTel enabled but the SDK/exporter is not installed "
                    "(pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http) "
                    "— telemetry disabled")
        return False
    try:
        resource = Resource.create({
            "service.name": settings.server_name,
            "service.version": settings.server_version,
        })
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
            endpoint=settings.otel_traces_endpoint(),
            headers=settings.otel_headers(),
        )))
        trace.set_tracer_provider(provider)
        log.info("OTel tracing enabled → %s (service=%s)",
                 settings.otel_traces_endpoint(), settings.server_name)
        return True
    except Exception:  # noqa: BLE001 — telemetry setup must never break the server
        log.warning("OTel setup failed — continuing without telemetry", exc_info=True)
        return False


def get_tracer():
    """The OTel API tracer (no-op when no provider is installed). None if the API isn't
    importable, so callers can cheaply skip span work."""
    try:
        from opentelemetry import trace
        return trace.get_tracer(_INSTRUMENTATION)
    except Exception:  # noqa: BLE001
        return None
