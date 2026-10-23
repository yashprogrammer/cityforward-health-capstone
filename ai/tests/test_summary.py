import pytest

from copilot import summary
from copilot.chart import load_chart
from copilot.gateway import Gateway
from copilot.llm import LLMError
from tests.conftest import ScriptedLLM


@pytest.fixture(scope="module")
def hale_chart(rt):
    pid = rt.fhir.read("Encounter", "IP2610-0142")["subject"]["reference"].split("/")[1]
    return load_chart(rt.fhir, pid)


def _good(facts):
    trop = next(k for k, f in facts.items() if "Troponin" in f.text)
    lpa = next(k for k, f in facts.items() if "NO RESULT YET" in f.text)
    return {"headline": "Anterior STEMI treated with PCI", "course": "Admitted 10/17 with STEMI and underwent PCI.", "history": "Hypertension and hyperlipidemia.",
            "findings": [{"text": "Troponin I trended down to 3100 ng/L", "sources": [trop]}], "follow_up": [{"text": "Lipoprotein (a) result is still pending", "sources": [lpa]}]}


def test_evidence_is_the_current_stay_and_the_pending_result_is_visible(hale_chart):
    facts = summary.select_facts(hale_chart)
    assert any("NO RESULT YET" in f.text for f in facts.values())
    assert list(facts)[0] == "E1" and len(facts) <= 60
    assert all(f.ref for f in facts.values())


def test_good_summary_is_grounded_and_sources_map_to_fhir_refs(hale_chart):
    facts = summary.select_facts(hale_chart)
    res = summary.summarize(Gateway(ScriptedLLM(_good(facts))), hale_chart)
    assert res.mode == "llm"
    cited = res.summary.findings[0].sources[0]
    assert res.sources[cited].ref.startswith("Observation/")


def test_invented_number_is_revised_once_then_falls_back(hale_chart):
    facts = summary.select_facts(hale_chart)
    bad = _good(facts)
    bad["findings"][0]["text"] = "Troponin I fell to 77777 ng/L"
    llm = ScriptedLLM(bad, bad)
    res = summary.summarize(Gateway(llm), hale_chart)
    assert res.mode == "template" and res.problems and len(llm.prompts) == 2 and "77777" in llm.prompts[1][1]


def test_template_is_grounded_and_flags_the_pending_result(hale_chart):
    res = summary.summarize(Gateway(None), hale_chart)
    assert res.mode == "template" and any("NO RESULT YET" in s.text for s in res.sources.values() if s.id in res.summary.follow_up[0].sources)
    res2 = summary.summarize(Gateway(ScriptedLLM(LLMError("down"))), hale_chart)
    assert res2.mode == "template" and "unavailable" in res2.note
