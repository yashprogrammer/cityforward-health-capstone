"""The course's submission checker, run against this repository's own stack (both services in-process behind one fake origin)."""
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "course"))
import check_submission as cs   # noqa: E402


@pytest.fixture()
def origin(client, meditrack, rt):
    """One origin: /ai/* -> the Copilot (prefix stripped), everything else -> MediTrack, like the hospital gateway."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode()
        target, path = (client, path[3:] or "/") if path == "/ai" or path.startswith("/ai/") else (meditrack, path)
        r = target.request(request.method, path, content=request.content, headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")})
        return httpx.Response(r.status_code, headers={k: v for k, v in r.headers.items() if k.lower() not in ("content-length", "content-encoding", "transfer-encoding")}, content=r.content)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_correct_deployment_passes_every_remote_check(origin):
    results = cs.remote_checks(origin, "http://hospital.test")
    failed = [r for r in results if not r["ok"]]
    assert not failed, failed
    names = {r["check"] for r in results}
    assert {"copilot health", "FHIR capability statement", "SMART configuration advertises PKCE S256", "metrics are not exposed through the ingress", "unsigned webhook is rejected"} <= names


def test_an_unreachable_deployment_fails_cleanly():
    dead = httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused"))))
    res = cs.remote_checks(dead, "http://nowhere.test")
    assert res and not res[0]["ok"] and "ConnectError" in res[0]["detail"]


def test_evaluation_file_must_keep_the_safety_properties(tmp_path):
    good = ROOT / "ai" / "evalkit" / "baselines" / "offline" / "metrics.json"
    assert all(r["ok"] for r in cs.eval_checks(good))
    data = json.loads(good.read_text())
    data["metrics"]["ask.identifier_leaks"] = 2
    bad = tmp_path / "metrics.json"
    bad.write_text(json.dumps(data))
    failed = [r["check"] for r in cs.eval_checks(bad) if not r["ok"]]
    assert failed == ["safety gate ask.identifier_leaks == 0"]
    assert not cs.eval_checks(tmp_path / "missing.json")[0]["ok"]


def test_summary_must_be_honest(tmp_path):
    honest = tmp_path / "honest.md"
    honest.write_text("We built X. Gaps: no BAA yet, not measured on real notes.")
    boastful = tmp_path / "boast.md"
    boastful.write_text("Our system is HIPAA compliant and production-ready.")
    assert all(r["ok"] for r in cs.artifact_checks("https://example.com/readout", str(honest)))
    failed = [r["check"] for r in cs.artifact_checks("https://example.com/readout", str(boastful)) if not r["ok"]]
    assert "summary mentions what was not proven" in failed and "summary avoids unearned claims" in failed
    assert not cs.artifact_checks(None, str(honest))[0]["ok"]
