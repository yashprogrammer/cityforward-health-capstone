import pytest

from copilot import cache, db, rag, summary
from copilot.app import rt as runtime
from copilot.auth import SYSTEM, Principal
from copilot.chart import load_chart
from copilot.gateway import Gateway
from copilot.llm import LLMError, RoutedBackend, cost_usd, get_backend
from evalkit import budget
from tests.conftest import ScriptedLLM, bearer, user_token

HALE = "IP2610-0142"


@pytest.fixture()
def hale_chart(rt, clean_db):
    pid = rt.fhir.read("Encounter", HALE)["subject"]["reference"].split("/")[1]
    chart = load_chart(rt.fhir, pid)
    rag.index_chart(chart)
    return chart


def good(chart):
    facts = summary.select_facts(chart)
    trop = next(k for k, f in facts.items() if "Troponin" in f.text)
    return {"headline": "STEMI", "course": "Admitted with STEMI.", "history": "HTN.", "findings": [{"text": "Troponin I fell to 3100 ng/L", "sources": [trop]}], "follow_up": []}


def test_prices_are_per_million_tokens():
    assert cost_usd("openai/gpt-oss-20b", 1_000_000, 1_000_000) == pytest.approx(0.075 + 0.30)
    assert cost_usd("unknown:model", 0, 0) == 0


def test_summary_cache_saves_the_second_model_call(hale_chart):
    llm = ScriptedLLM(good(hale_chart), good(hale_chart))
    gw = Gateway(llm)
    first = summary.summarize_cached(gw, hale_chart, SYSTEM)
    second = summary.summarize_cached(gw, hale_chart, SYSTEM)
    assert not first.cached and second.cached and len(llm.prompts) == 1
    assert second.summary.headline == first.summary.headline
    # any change to the evidence changes the key: no stale summary
    hale_chart.documents[-1].text += " New finding documented this morning."
    third = summary.summarize_cached(gw, hale_chart, SYSTEM)
    assert not third.cached and len(llm.prompts) == 2
    # a different model is a different answer
    other = ScriptedLLM(good(hale_chart))
    other.name = "scripted:other"
    assert not summary.summarize_cached(Gateway(other), hale_chart, SYSTEM).cached


def test_fallbacks_are_never_cached(hale_chart):
    gw = Gateway(ScriptedLLM(LLMError("down")))
    assert summary.summarize_cached(gw, hale_chart, SYSTEM).mode == "template"
    with db.system() as c:
        assert c.execute("SELECT count(*) n FROM result_cache").fetchone()["n"] == 0


def test_cache_respects_row_level_security(hale_chart):
    summary.summarize_cached(Gateway(ScriptedLLM(good(hale_chart))), hale_chart, SYSTEM)
    key = cache.make_key("summary", summary.PROMPT_VERSION, "scripted:test", hale_chart.patient_id, [f.text for f in summary.select_facts(hale_chart).values()])
    assert cache.get("summary", key, Principal(user="x", role="NURSE", units=["3E-MED"]))is None
    assert cache.get("summary", key, Principal(user="y", role="NURSE", units=[hale_chart.unit])) is not None


def test_daily_budget_is_a_circuit_breaker_that_falls_back_to_no_ai(hale_chart):
    llm = ScriptedLLM(good(hale_chart), good(hale_chart), good(hale_chart))
    gw = Gateway(llm, daily_budget_usd=0.0001)
    first = summary.summarize(gw, hale_chart, SYSTEM)
    assert first.mode == "llm"
    assert gw.spend_today(max_age=0) > 0
    second = summary.summarize(gw, hale_chart, SYSTEM)                # cap reached: the model is not called
    assert second.mode == "template" and "budget" in second.note and len(llm.prompts) == 1
    from copilot import audit
    assert any(r["action"] == "ai.budget-exhausted" for r in audit.search(action="ai.budget-exhausted"))
    from copilot import telemetry
    assert "copilot_budget_blocked_total" in telemetry.metrics_text().decode()


def test_different_models_for_different_jobs(hale_chart, monkeypatch):
    cheap, strong = ScriptedLLM(good(hale_chart)), ScriptedLLM(good(hale_chart))
    cheap.name, strong.name = "scripted:cheap", "scripted:strong"
    gw = Gateway(RoutedBackend(strong, {"coding": cheap}))
    summary.summarize(gw, hale_chart, SYSTEM)                          # purpose "summary" -> default (strong)
    assert len(strong.prompts) == 1 and not cheap.prompts
    gw.json("s", "u", summary.Draft, purpose="coding")                 # purpose "coding" -> the cheap model
    with db.system() as c:
        models = {r["purpose"]: r["model"] for r in c.execute("SELECT purpose, model FROM llm_call").fetchall()}
    assert models["summary"] == "scripted:strong" and models["coding"] == "scripted:cheap"


def test_env_overrides_build_a_routed_backend(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL_CODING", "openai/gpt-oss-120b")
    b = get_backend()
    assert isinstance(b, RoutedBackend) and b.for_purpose("coding").name.endswith("gpt-oss-120b") and b.for_purpose("summary").name.endswith("gpt-oss-20b")


def test_usage_endpoint_for_operators_only(rt, clean_db, client, meditrack, hale_chart):
    llm = ScriptedLLM(good(hale_chart))
    runtime.gateway = Gateway(llm, daily_budget_usd=5.0)
    summary.summarize_cached(runtime.gateway, hale_chart, SYSTEM)
    admin = bearer(user_token(meditrack, HALE, user="it.admin")["access_token"])
    r = client.get("/api/usage", headers=admin)
    assert r.status_code == 200
    d = r.json()
    row = next(x for x in d["by_purpose"] if x["purpose"] == "summary")
    assert row["calls"] == 1 and row["tokens_in"] > 0 and row["p95_ms"] >= 0 and d["daily_budget_usd"] == 5.0 and d["cache"][0]["feature"] == "summary"
    assert client.get("/api/usage", headers=bearer(user_token(meditrack, HALE)["access_token"])).status_code == 403


def test_budget_model_prices_the_measured_prompts(rt, payer):
    sizes = budget.measure(rt)
    assert set(sizes) == {"ask", "summary", "prior-auth", "coding"} and all(300 < v < 6000 for v in sizes.values())
    rows = {r["model"]: r for r in budget.table(sizes)}
    small, big = rows["openai/gpt-oss-20b"], rows["openai/gpt-oss-120b"]
    assert small["usd_per_discharge"] < big["usd_per_discharge"] and small["within_budget"]
    assert all(d["est_latency_s"] < d["budget_s"] for d in small["by_purpose"])
