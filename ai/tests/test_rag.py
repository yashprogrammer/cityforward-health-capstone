import pytest

from copilot import ask as ask_mod
from copilot import rag
from copilot.app import rt as runtime
from copilot.chart import load_chart, split_text
from copilot.embeddings import HashEmbedder
from copilot.fhir_client import FhirClient, FhirError
from copilot.gateway import Gateway
from copilot.llm import LLMError
from copilot.auth import SYSTEM
from tests.conftest import ScriptedLLM, bearer, user_token

HALE_ENC = "IP2610-0142"


@pytest.fixture(scope="module")
def hale(rt, meditrack):
    enc = rt.fhir.read("Encounter", HALE_ENC)
    return enc["subject"]["reference"].split("/")[1]


def test_backend_services_auth_and_fhir_read(rt, hale):
    assert rt.fhir.read("Patient", hale)["name"][0]["family"] == "Hale"
    bad = FhirClient(http=rt.fhir.http, base="http://testserver", key_pem=rt.fhir.key_pem, client_id="nobody")
    with pytest.raises(FhirError) as e:
        bad.token()
    assert e.value.status == 401


def test_chart_loader_reads_notes_and_documents(rt, hale):
    chart = load_chart(rt.fhir, hale)
    assert (chart.name, chart.age, chart.unit) == ("Robert Hale", 62, "4W-CARD")
    assert chart.payer == "Summit Health Plan"
    assert any("radial" in d.text.lower() for d in chart.documents)
    assert any(f.kind == "lab" and "NO RESULT YET" in f.text for f in chart.facts())
    assert any(d.mime == "application/pdf" and d.text for d in chart.documents)   # scanned PDFs have a text layer in the demo data


def test_split_text_respects_paragraphs():
    parts = split_text("A. " * 10 + "\n\n" + "B. " * 10, 40)
    assert len(parts) >= 2 and all(len(p) < 90 for p in parts)


def test_hash_embedder_is_deterministic_and_similar_texts_are_closer():
    e = HashEmbedder()
    a, b, c = e.embed(["chest pain troponin elevated", "elevated troponin with chest pain", "knee replacement physiotherapy"])
    dot = lambda x, y: sum(i * j for i, j in zip(x, y))   # noqa: E731
    assert abs(dot(a, a) - 1) < 1e-6 and dot(a, b) > dot(a, c)
    assert e.embed(["chest pain troponin elevated"])[0] == a


def test_indexing_is_idempotent_and_replaces_changed_text(rt, clean_db, hale):
    chart = load_chart(rt.fhir, hale)
    first = rag.index_chart(chart)
    again = rag.index_chart(chart)
    assert first["chunks"] > 30 and first["added"] == first["chunks"]
    assert again["added"] == 0 and again["removed"] == 0
    chart.documents[0].text += " Added a new sentence about warfarin."
    third = rag.index_chart(chart)
    assert third["added"] >= 1 and third["removed"] >= 1


@pytest.mark.parametrize("question,expect", [
    ("Is the lipoprotein(a) result back?", "NO RESULT YET"),
    ("What was the troponin trend?", "Troponin"),
    ("How was the procedure done, radial or femoral access?", "radial"),
    ("How often is ticagrelor given?", "twice daily"),
])
def test_hybrid_retrieval_finds_the_right_passage(rt, clean_db, hale, question, expect):
    rag.index_chart(load_chart(rt.fhir, hale))
    hits = rag.search(hale, question, k=5, principal=SYSTEM)
    assert any(expect.lower() in h.text.lower() for h in hits), [h.text[:60] for h in hits]


def test_retrieval_is_hard_scoped_to_one_patient(rt, clean_db, hale):
    other = rt.fhir.read("Encounter", "IP2610-0151")["subject"]["reference"].split("/")[1]
    assert other != hale
    rag.index_chart(load_chart(rt.fhir, hale))
    rag.index_chart(load_chart(rt.fhir, other))
    assert all(h.source_ref for h in rag.search(hale, "hip fracture hemiarthroplasty walker", k=20, principal=SYSTEM))
    assert not any("hemiarthroplasty" in h.text.lower() for h in rag.search(hale, "hip fracture hemiarthroplasty walker", k=20, principal=SYSTEM))


def _gw(*replies):
    llm = ScriptedLLM(*replies)
    return Gateway(llm), llm


def test_ask_cited_answer_passes_grounding(rt, clean_db, hale):
    rag.index_chart(load_chart(rt.fhir, hale))
    hits = rag.search(hale, "lipoprotein(a) pending", k=ask_mod.K, principal=SYSTEM)
    cid = next(f"C{i + 1}" for i, h in enumerate(hits) if "NO RESULT YET" in h.text)
    gw, llm = _gw({"answer": "The Lp(a) result is still pending.", "citations": [cid], "insufficient_evidence": False})
    res = ask_mod.ask(gw, hale, "lipoprotein(a) pending", principal=SYSTEM)
    assert res.mode == "llm" and res.citations and "NO RESULT YET" in res.citations[0].text
    assert "PASSAGES" in llm.prompts[0][1]


def test_ask_wrong_number_is_revised_then_extractive(rt, clean_db, hale):
    rag.index_chart(load_chart(rt.fhir, hale))
    bad = {"answer": "Troponin peaked at 99999 ng/L.", "citations": ["C1"], "insufficient_evidence": False}
    res = ask_mod.ask(*_gw(bad, bad)[:1], hale, "troponin trend", principal=SYSTEM)
    assert res.mode == "extractive" and res.problems and res.citations


def test_ask_honest_insufficient_evidence_is_accepted(rt, clean_db, hale):
    rag.index_chart(load_chart(rt.fhir, hale))
    res = ask_mod.ask(_gw({"answer": "The chart does not say.", "citations": [], "insufficient_evidence": True})[0], hale, "Does he have a penicillin allergy?", principal=SYSTEM)
    assert res.mode == "llm" and res.insufficient_evidence


def test_ask_model_outage_degrades_to_passages(rt, clean_db, hale):
    rag.index_chart(load_chart(rt.fhir, hale))
    res = ask_mod.ask(_gw(LLMError("503"))[0], hale, "aspirin dose", principal=SYSTEM)
    assert res.mode == "extractive" and "unavailable" in res.note


def test_api_requires_a_valid_token_and_enforces_unit_and_patient(rt, clean_db, client, meditrack, hale):
    assert client.post(f"/api/patients/{hale}/ask", json={"question": "x"}).status_code == 401
    assert client.post(f"/api/patients/{hale}/ask", headers=bearer("garbage"), json={"question": "x"}).status_code == 401
    tok = user_token(meditrack, HALE_ENC)["access_token"]                      # Dr. Raman, 4W-CARD, launched on Hale
    assert client.post(f"/api/patients/{hale}/ask", headers=bearer(tok), json={"question": "x"}).status_code == 409   # not indexed yet
    r = client.post(f"/api/patients/{hale}/index", headers=bearer(tok))
    assert r.status_code == 200 and r.json()["chunks"] > 30
    runtime.gateway = Gateway(None)
    ok = client.post(f"/api/patients/{hale}/ask", headers=bearer(tok), json={"question": "aspirin dose"})
    assert ok.status_code == 200 and ok.json()["mode"] == "extractive"
    other = rt.fhir.read("Encounter", "IP2610-0151")["subject"]["reference"].split("/")[1]
    assert client.post(f"/api/patients/{other}/ask", headers=bearer(tok), json={"question": "x"}).status_code == 403   # launch context is Hale only
    nurse = user_token(meditrack, HALE_ENC, user="nurse.park")["access_token"]                # 3E-MED nurse, Hale is on 4W-CARD
    assert client.post(f"/api/patients/{hale}/ask", headers=bearer(nurse), json={"question": "x"}).status_code == 403


def test_llm_calls_are_metered_without_storing_prompts(rt, clean_db, hale):
    from copilot import db
    rag.index_chart(load_chart(rt.fhir, hale))
    ask_mod.ask(_gw({"answer": "x", "citations": [], "insufficient_evidence": True})[0], hale, "anything", principal=SYSTEM)
    with db.session() as c:
        row = c.execute("SELECT * FROM llm_call").fetchone()
    assert row["purpose"] == "ask" and row["tokens_in"] > 0 and row["status"] == "ok" and "text" not in row and "prompt" not in row
