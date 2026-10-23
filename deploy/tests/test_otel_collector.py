"""Run the REAL collector image with the repo's processor chain and prove PHI does not survive it (Docker required)."""
import copy
import json
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import pytest
import yaml

VALUES = Path(__file__).resolve().parents[1] / "platform" / "otel" / "collector-values.yaml"
IMAGE = "otel/opentelemetry-collector-contrib:0.116.1"


def _have_image() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _have_image(), reason="collector image not available (docker pull " + IMAGE + ")")


def test_collector_strips_phi_from_traces():
    cfg = yaml.safe_load(VALUES.read_text())["config"]
    cfg = copy.deepcopy(cfg)
    # same processors, but write to a file instead of Jaeger so the test can read what would have been exported
    cfg["exporters"] = {"file": {"path": "/out/traces.json"}}
    cfg["connectors"] = {}
    cfg["processors"]["batch"] = {"timeout": "200ms"}
    cfg["service"] = {"pipelines": {"traces": {"receivers": ["otlp"], "processors": ["attributes/drop-sensitive", "transform/paths", "redaction", "batch"], "exporters": ["file"]}}}
    out = Path(tempfile.mkdtemp())
    out.chmod(0o777)
    conf = out / "config.yaml"
    conf.write_text(yaml.safe_dump(cfg))
    name = f"otel-test-{uuid.uuid4().hex[:6]}"
    subprocess.run(["docker", "run", "-d", "--name", name, "-p", "127.0.0.1::4318", "-v", f"{conf}:/etc/otelcol/config.yaml:ro", "-v", f"{out}:/out", IMAGE, "--config=/etc/otelcol/config.yaml"], check=True, capture_output=True)
    try:
        port = subprocess.run(["docker", "port", name, "4318/tcp"], capture_output=True, text=True, check=True).stdout.split(":")[-1].strip()
        url = f"http://127.0.0.1:{port}/v1/traces"
        for _ in range(40):
            try:
                httpx.post(url, json={"resourceSpans": []}, timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.5)

        def attr(k, v):
            return {"key": k, "value": {"stringValue": v}}
        span = {"traceId": "0af7651916cd43dd8448eb211c80319c", "spanId": "b7ad6b7169203331", "name": "POST /api/patients/12345/ask", "kind": 2,
                "startTimeUnixNano": "1700000000000000000", "endTimeUnixNano": "1700000001000000000",
                "attributes": [attr("url.path", "/api/patients/12345/ask"), attr("http.target", "/api/patients/12345/ask?mrn=CF306377"),
                               attr("http.request.header.authorization", "Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig"), attr("user.email", "dr.raman@cityforward.example"),
                               attr("note", "SSN 999-12-3456 phone 614-555-0192 account IP2610-0142 born 1964-03-02 mail a@b.org"), attr("db.statement", "SELECT * FROM patient WHERE name='Robert Hale'"),
                               attr("llm.purpose", "ask"), attr("llm.tokens_in", "812")]}
        body = {"resourceSpans": [{"resource": {"attributes": [attr("service.name", "copilot")]}, "scopeSpans": [{"scope": {"name": "t"}, "spans": [span]}]}]}
        assert httpx.post(url, json=body, timeout=5).status_code == 200
        exported = ""
        for _ in range(40):
            f = out / "traces.json"
            if f.exists() and f.stat().st_size:
                exported = f.read_text()
                break
            time.sleep(0.5)
        assert exported, subprocess.run(["docker", "logs", name], capture_output=True, text=True).stderr[-800:]
        for leak in ("12345", "CF306377", "eyJhbGci", "dr.raman@", "999-12-3456", "614-555-0192", "IP2610-0142", "1964-03-02", "a@b.org", "Robert Hale", "SELECT"):
            assert leak not in exported, f"{leak!r} survived the collector"
        assert "/api/patients/{id}/ask" in exported                   # the useful part is kept
        assert "llm.purpose" in exported and "812" in exported        # operational attributes are untouched
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
