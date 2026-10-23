"""Observability without PHI: traces (OpenTelemetry -> collector -> Jaeger) and metrics (Prometheus).

Rules: spans and metric labels carry operation names, model names, token counts, outcomes and opaque ids' *shapes* (route templates), never patient ids in URLs,
never prompts, replies or chart text. A second line of defence lives in the collector (platform/otel/collector-values.yaml) in case a developer adds an attribute carelessly.
Metrics are served on their own port (not through the ingress), so nothing outside the cluster can read them.
"""
import os
import re

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest, start_http_server

REGISTRY = CollectorRegistry()
LLM_CALLS = Counter("copilot_llm_calls_total", "Model calls", ["purpose", "model", "status"], registry=REGISTRY)
LLM_TOKENS = Counter("copilot_llm_tokens_total", "Tokens sent to / received from the model", ["purpose", "direction"], registry=REGISTRY)
LLM_COST = Counter("copilot_llm_cost_usd_total", "Estimated model spend", ["purpose"], registry=REGISTRY)
LLM_SECONDS = Histogram("copilot_llm_latency_seconds", "Model call latency", ["purpose"], buckets=(.25, .5, 1, 2, 4, 8, 16, 32), registry=REGISTRY)
UNREDACTED = Counter("copilot_unredacted_calls_total", "Model calls that carried a prompt with nothing redacted (every patient prompt contains at least the patient's name, so this should stay 0)", ["purpose"], registry=REGISTRY)
REDACTIONS = Counter("copilot_redactions_total", "Identifiers replaced before a model call", ["purpose"], registry=REGISTRY)
FALLBACKS = Counter("copilot_fallback_total", "AI outputs replaced by the no-AI fallback", ["feature"], registry=REGISTRY)
ACCESS = Counter("copilot_access_total", "Authorization decisions", ["action", "outcome"], registry=REGISTRY)
EVENTS = Counter("copilot_events_total", "EHR events processed", ["type", "status"], registry=REGISTRY)
BUDGET_BLOCKED = Counter("copilot_budget_blocked_total", "Model calls refused because the daily budget was reached", registry=REGISTRY)
CACHE = Counter("copilot_cache_total", "Result cache lookups", ["feature", "outcome"], registry=REGISTRY)
CANARY_OK = Gauge("copilot_redaction_canary_ok", "1 when the live gateway removes a synthetic patient's identifiers from a prompt, 0 when it does not", registry=REGISTRY)
AUDIT_FAILURES = Counter("copilot_audit_failures_total", "Audit records that could not be written", registry=REGISTRY)
HTTP_SECONDS = Histogram("copilot_http_request_seconds", "API latency by route template", ["method", "route", "status"], registry=REGISTRY,
                         buckets=(.05, .1, .25, .5, 1, 2, 5, 10, 30))

_ID = re.compile(r"/(patients|prior-auth|coding|events|break-glass)/[^/?#]+")


def scrub_path(path: str) -> str:
    """/api/patients/12345/ask -> /api/patients/{id}/ask : identifiers never go into trace attributes."""
    return _ID.sub(lambda m: f"/{m.group(1)}/{{id}}", path)


def record_llm(r: dict) -> None:
    LLM_CALLS.labels(r["purpose"], r["model"], r["status"]).inc()
    LLM_TOKENS.labels(r["purpose"], "in").inc(r["tokens_in"])
    LLM_TOKENS.labels(r["purpose"], "out").inc(r["tokens_out"])
    LLM_COST.labels(r["purpose"]).inc(float(r["cost_usd"]))
    LLM_SECONDS.labels(r["purpose"]).observe(r["ms"] / 1000)
    if r["redactions"]:
        REDACTIONS.labels(r["purpose"]).inc(r["redactions"])
    else:
        UNREDACTED.labels(r["purpose"]).inc()


def metrics_text() -> bytes:
    return generate_latest(REGISTRY)


_tracer = None


def tracer():
    from opentelemetry import trace
    return trace.get_tracer("copilot")


def _scrub_hook(span, scope):
    """Server-request hook: replace the real path by its template in every attribute that carries it."""
    if span and span.is_recording():
        path = scrub_path(scope.get("path", ""))
        for key in ("http.target", "url.path", "http.url", "url.full"):
            span.set_attribute(key, path)


def setup(app, provider=None) -> bool:
    """Wire tracing when an OTLP endpoint is configured (or a test passes its own provider). Returns whether tracing is on."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if provider is None and not endpoint:
        return False
    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    if provider is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        provider = TracerProvider(resource=Resource.create({"service.name": "copilot", "service.namespace": "cityforward", "deployment.environment": os.getenv("DEPLOY_ENV", "dev")}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, server_request_hook=_scrub_hook, excluded_urls="healthz")
    HTTPXClientInstrumentor().instrument(tracer_provider=provider, request_hook=lambda span, req: span.set_attribute("url.full", scrub_path(str(req.url).split("?")[0])) if span.is_recording() else None)
    return True


def start_metrics_server() -> None:
    port = int(os.getenv("METRICS_PORT", "0"))
    if port:
        start_http_server(port, registry=REGISTRY)
