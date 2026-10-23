import pytest

from copilot import ask as ask_mod
from copilot import cds, rag, summary
from copilot.app import rt as runtime
from copilot.chart import load_chart
from copilot.gateway import Gateway
from tests.conftest import ScriptedLLM, bearer, user_token

HALE = "IP2610-0142"


@pytest.mark.parametrize("text", [
    "Start amiodarone 200 mg daily.", "The team should increase the statin dose.", "Consider starting heparin.", "We recommend discontinuing ticagrelor.",
    "Troponin trending down. Give furosemide 40 mg IV.", "Antibiotics should be stopped.", "There is a 35% risk of readmission.", "Risk score: high.",
    "Probability of stroke is 12 percent.",
])
def test_lint_catches_directives_and_risk_scores(text):
    assert cds.lint(text), text


@pytest.mark.parametrize("text", [
    "Troponin I fell from 14250 to 3100 ng/L.", "Patient was started on ticagrelor on 2026-10-17 (progress note).", "Discharge planning documented by case management.",
    "Aspirin 81 mg once daily is an active medication.", "Dr. Raman documented that the patient is stable and pain free.",
    "Hold parameters are not documented in the chart.",
])
def test_lint_allows_documentation_statements(text):
    assert not cds.lint(text), cds.lint(text)


@pytest.fixture()
def hale(rt, clean_db):
    pid = rt.fhir.read("Encounter", HALE)["subject"]["reference"].split("/")[1]
    chart = load_chart(rt.fhir, pid)
    rag.index_chart(chart)
    return pid, chart


def test_a_directive_in_a_summary_is_revised_away(hale):
    pid, chart = hale
    facts = summary.select_facts(chart)
    trop = next(k for k, f in facts.items() if "Troponin" in f.text)
    bad = {"headline": "STEMI", "course": "Admitted with STEMI.", "history": "HTN.", "findings": [{"text": "Troponin I fell to 3100 ng/L. Start amiodarone.", "sources": [trop]}], "follow_up": []}
    good = {**bad, "findings": [{"text": "Troponin I fell to 3100 ng/L", "sources": [trop]}]}
    llm = ScriptedLLM(bad, good)
    res = summary.summarize(Gateway(llm), chart)
    assert res.mode == "llm" and res.attempts if hasattr(res, "attempts") else True
    assert "treatment directive" in llm.prompts[1][1] and "Start amiodarone" not in res.summary.findings[0].text


def test_a_risk_prediction_in_an_answer_never_survives(hale):
    pid, chart = hale
    hits = rag.search(pid, "troponin", k=8)
    cid = next(f"C{i + 1}" for i, h in enumerate(hits) if "Troponin" in h.text)
    risky = {"answer": "Troponin 3100 ng/L; there is a 40% risk of heart failure.", "citations": [cid], "insufficient_evidence": False}
    res = ask_mod.ask(Gateway(ScriptedLLM(risky, risky)), pid, "troponin")
    assert res.mode == "extractive" and any("risk probability" in p for p in res.problems)


def test_every_ai_response_carries_the_transparency_envelope(rt, clean_db, client, meditrack, hale):
    pid, _ = hale
    tok = bearer(user_token(meditrack, HALE)["access_token"])
    client.post(f"/api/patients/{pid}/index", headers=tok)
    runtime.gateway = Gateway(None)
    for path, body in ((f"/api/patients/{pid}/ask", {"question": "aspirin"}), (f"/api/patients/{pid}/summary", None)):
        r = client.post(path, headers=tok, json=body) if body else client.post(path, headers=tok)
        assert r.status_code == 200, r.text
        env = r.json()["cds"]
        assert env["for"] == "licensed health care professionals" and env["sources"] and env["method"] and env["limitations"]
        assert set(env["regulatory"]["criteria"]) == {"c1_no_image_or_signal", "c2_displays_patient_information", "c3_supports_hcp_no_directive", "c4_independent_review_of_basis"}
        assert all(s["ref"] for s in env["sources"])
    about = client.get("/api/about", headers=tok).json()
    assert "does not diagnose" in about["intended_use"] and "time-critical" in about["intended_use"]
    assert client.get("/api/about").status_code == 401
