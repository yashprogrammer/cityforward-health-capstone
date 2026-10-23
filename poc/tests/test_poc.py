import pytest

from summarizer.graph import HistorySummary, build_evidence, check_grounding, summarize
from summarizer.llm import LLMError, extract_json, get_llm
from summarizer.records import list_bundles, load_bundle
from tests.conftest import BUNDLES


class Scripted:
    """A fake model that replays canned replies (dicts) or raises."""
    name = "scripted"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete_json(self, system, user):
        self.calls.append(user)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(scope="module")
def hale():
    return load_bundle(BUNDLES / "GLD-001.json")


def good(ev):
    tn = next(k for k, v in ev.items() if "Troponin" in v)
    return {"headline": "STEMI s/p PCI", "course": "Admitted with anterior STEMI and treated with PCI.", "history": "Hypertension, hyperlipidemia.",
            "findings": [{"text": "Troponin I fell to 3100 ng/L", "sources": [tn]}], "follow_up": []}


def test_loader_resolves_a_synthea_and_a_golden_bundle(hale):
    assert (hale.name, hale.age, hale.sex) == ("Robert Hale", 62, "M")
    assert any("myocardial" in c.display.lower() for c in hale.problem_list())
    assert any("aspirin" in m.name.lower() for m in hale.active_medications())
    assert len(list_bundles(BUNDLES)) >= 20
    syn = load_bundle(BUNDLES / "SYN-004.json")
    assert syn.notes and syn.observations


def test_pending_result_is_visible_in_evidence(hale):
    assert any("Lipoprotein (a)" in v and "pending" in v for v in build_evidence(hale).values())


def test_offline_gives_a_labelled_template(hale):
    res = summarize(hale, None)
    assert res.mode == "template" and "no model" in res.note
    assert check_grounding(res.summary, res.evidence) == []


def test_good_model_reply_passes_first_time(hale):
    ev = build_evidence(hale)
    llm = Scripted(good(ev))
    res = summarize(hale, llm)
    assert res.mode == "llm" and res.attempts == 0 and len(llm.calls) == 1


def test_invented_number_is_sent_back_and_fixed(hale):
    ev = build_evidence(hale)
    bad = good(ev)
    bad["findings"][0]["text"] = "Troponin I fell to 9999 ng/L"
    llm = Scripted(bad, good(ev))
    res = summarize(hale, llm)
    assert res.mode == "llm" and res.attempts == 1
    assert "9999" in llm.calls[1] and "does not appear" in llm.calls[1]


def test_unknown_citation_twice_falls_back_to_template(hale):
    ev = build_evidence(hale)
    bad = good(ev)
    bad["findings"][0]["sources"] = ["E999"]
    res = summarize(hale, Scripted(bad, bad))
    assert res.mode == "template" and "grounding" in res.note and res.problems


def test_model_outage_falls_back_without_crashing(hale):
    res = summarize(hale, Scripted(LLMError("503 upstream")))
    assert res.mode == "template" and "unavailable" in res.note


def test_malformed_schema_is_treated_as_a_failed_call(hale):
    res = summarize(hale, Scripted({"headline": "x"}))
    assert res.mode == "template"


def test_uncited_statement_is_rejected(hale):
    s = HistorySummary(headline="h", course="c", history="h", findings=[{"text": "The patient is fine", "sources": []}])
    assert any("no citation" in p for p in check_grounding(s, build_evidence(hale)))


def test_extract_json_handles_fences_and_prose():
    assert extract_json('Sure!\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    with pytest.raises(LLMError):
        extract_json("no json here")


def test_provider_switch(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert get_llm() is None
    with pytest.raises(LLMError):
        get_llm("groq")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    assert get_llm().name == "groq:openai/gpt-oss-20b"


def test_openai_compatible_client_against_a_fake_endpoint(hale):
    """The real client path (LangChain ChatOpenAI over HTTP) against a local fake of the chat-completions API."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    ev = build_evidence(hale)
    reply = "Here you go:\n```json\n" + json.dumps(good(ev)) + "\n```"

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                               "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": reply}}],
                               "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from summarizer.llm import OpenAICompatible
        res = summarize(hale, OpenAICompatible("fake", f"http://127.0.0.1:{srv.server_port}/v1", "k", "m"))
    finally:
        srv.shutdown()
    assert res.mode == "llm" and res.model == "fake:m"


def test_streamlit_app_renders_offline(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    at = AppTest.from_file(str(__import__("pathlib").Path(__file__).resolve().parent.parent / "app.py"), default_timeout=30).run()
    assert not at.exception
    at.sidebar.button[0].click().run()
    assert not at.exception and any("Template summary" in w.value for w in at.warning)
