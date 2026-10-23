import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from copilot import ask as ask_mod
from copilot import rag, telemetry
from copilot.app import rt as runtime
from copilot.auth import SYSTEM
from copilot.chart import load_chart
from copilot.gateway import Gateway
from copilot.redact import Redactor
from tests.conftest import ScriptedLLM, bearer, user_token

HALE = "IP2610-0142"


def test_path_scrubbing():
    assert telemetry.scrub_path("/api/patients/12345/ask") == "/api/patients/{id}/ask"
    assert telemetry.scrub_path("/api/prior-auth/PA-AB12/requirements/spo2") == "/api/prior-auth/{id}/requirements/spo2"
    assert telemetry.scrub_path("/healthz") == "/healthz"


def test_traces_carry_route_templates_never_patient_ids():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    mini = FastAPI()

    @mini.post("/api/patients/{pid}/ask")
    def ask(pid: str):
        return {"ok": True}

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(mini, tracer_provider=provider, server_request_hook=telemetry._scrub_hook)
    TestClient(mini).post("/api/patients/987654321/ask?x=1")
    spans = exporter.get_finished_spans()
    assert spans
    blob = " ".join(str(v) for s in spans for v in [s.name, *s.attributes.values()])
    assert "987654321" not in blob and "/api/patients/{id}/ask" in blob


def test_metrics_record_access_ai_calls_redactions_and_fallbacks(rt, clean_db, client, meditrack):
    pid = rt.fhir.read("Encounter", HALE)["subject"]["reference"].split("/")[1]
    rag.index_chart(load_chart(rt.fhir, pid))
    tok = bearer(user_token(meditrack, HALE)["access_token"])
    before = telemetry.metrics_text().decode()
    runtime.gateway = Gateway(ScriptedLLM({"answer": "x", "citations": [], "insufficient_evidence": True}), redactor=Redactor(rag.identity_of))
    assert client.post(f"/api/patients/{pid}/ask", headers=tok, json={"question": "aspirin dose?"}).status_code == 200
    runtime.gateway = Gateway(None)
    assert client.post(f"/api/patients/{pid}/ask", headers=tok, json={"question": "aspirin dose?"}).status_code == 200
    nurse = bearer(user_token(meditrack, HALE, user="nurse.park")["access_token"])
    assert client.post(f"/api/patients/{pid}/ask", headers=nurse, json={"question": "x"}).status_code == 403
    text = telemetry.metrics_text().decode()

    def val(name, labels):
        m = re.search(rf'^{name}{{[^}}]*{labels}[^}}]*}} ([\d.e+-]+)$', text, flags=re.M)
        return float(m.group(1)) if m else 0.0
    assert val("copilot_access_total", 'action="ask",outcome="allowed"') >= 2
    assert val("copilot_access_total", 'action="ask",outcome="denied"') >= 1
    assert val("copilot_llm_calls_total", 'purpose="ask"') >= 1
    assert val("copilot_fallback_total", 'feature="ask"') >= 1
    assert re.search(r'copilot_http_request_seconds_count{[^}]*route="/api/patients/{patient_id}/ask"', text)       # route template, not the id
    assert not re.search(r"/patients/\d+", text) and "Hale" not in text                                                                     # no patient data in any metric
    assert text != before
