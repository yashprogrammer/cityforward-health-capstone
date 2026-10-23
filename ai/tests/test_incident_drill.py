import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from copilot import canary, telemetry                    # noqa: E402
from copilot.gateway import Gateway                      # noqa: E402
from copilot.redact import Redactor                      # noqa: E402
from copilot import rag                                  # noqa: E402
from incident import drill                               # noqa: E402
from tests.conftest import ScriptedLLM                   # noqa: E402


def test_canary_passes_on_a_healthy_gateway_and_fails_when_redaction_is_missing(rt):
    ok = canary.run(Gateway(ScriptedLLM(), redactor=Redactor()))
    assert ok["ok"] and ok["leaked"] == []
    bad = canary.run(Gateway(ScriptedLLM(), redactor=None))
    assert not bad["ok"] and "CF999999" in bad["leaked"] and "Zzyzx Quillfeather" in bad["leaked"]
    assert "copilot_redaction_canary_ok 0.0" in telemetry.metrics_text().decode()
    canary.run(Gateway(ScriptedLLM(), redactor=Redactor()))
    assert "copilot_redaction_canary_ok 1.0" in telemetry.metrics_text().decode()


def test_dry_run_sends_nothing_to_the_model(rt):
    llm = ScriptedLLM()
    gw = Gateway(llm, redactor=Redactor())
    text = gw.dry_run("s", "Zzyzx Quillfeather CF999999", context=type("C", (), {"identity": canary.CANARY})())
    assert "[PATIENT]" in text and not llm.prompts


def test_the_whole_drill_runs_and_tells_the_story(rt, payer, clean_db):
    report = drill.run_drill(rt)
    phases = [t[1].split()[0] for t in report["timeline"]]
    assert phases[:2] == ["T0", "T1"] and {"T2", "T3", "T4", "T5"} <= set(phases)
    # the bad deploy really leaked, in every patient's prompt, with every feature "working"
    assert all(report["leaks_during_incident"].values())
    assert report["canary"] == {"before": True, "during": False, "after": True}
    assert report["detection"]["firing"] is True
    sc = report["scope"]
    assert sc["calls"] == 3 and len(sc["patients"]) == 3 and sc["purposes"] == ["summary"] and sc["models"] == ["spy:model"]
    text = drill.render(report)
    assert "Breach risk assessment worksheet" in text and "no later than" in text and "redaction canary" in text


def test_detection_query_needs_traffic_but_the_canary_does_not(rt, clean_db):
    assert drill.detect_redaction_gap()["firing"] is False                       # no traffic: the metric alert is blind...
    assert canary.run(Gateway(ScriptedLLM(), redactor=None))["ok"] is False      # ...the canary is not


def test_a_real_model_client_cannot_be_wired_without_redaction(monkeypatch):
    from copilot.llm import OpenAICompatible
    client = OpenAICompatible("groq", "https://api.groq.com/openai/v1", "k", "openai/gpt-oss-20b")
    with pytest.raises(ValueError, match="without de-identification"):
        Gateway(client)                                                        # the bug from the drill is now a startup error
    assert Gateway(client, redactor=Redactor()).online
    monkeypatch.setenv("LLM_IN_BOUNDARY", "1")                                 # an operator states the model is inside the hospital boundary
    assert Gateway(OpenAICompatible("vllm", "http://vllm.internal/v1", "k", "m")).online
