import time

import httpx
import pytest

from copilot import db, discharge, events, priorauth_api
from copilot.app import rt as runtime
from copilot.auth import SYSTEM, Principal
from copilot.chart import load_chart
from copilot.gateway import Gateway
from copilot.payer_client import PayerClient
from tests.conftest import bearer, user_token

DOYLE, HALE = "IP2610-0151", "IP2610-0138"
WORKER = events.WORKER


def pid_of(rt, ip):
    return rt.fhir.read("Encounter", ip)["subject"]["reference"].split("/")[1]


@pytest.fixture()
def doyle(rt, payer, clean_db):
    chart = load_chart(rt.fhir, pid_of(rt, DOYLE))
    from copilot import rag
    rag.index_chart(chart)
    return chart


def slow(name, needs, seconds=0.4, fail=False):
    def run(ctx):
        time.sleep(seconds)
        if fail:
            raise RuntimeError(f"{name} blew up")
        return discharge.AgentResult(agent=name, status="ok", detail="done")
    return discharge.Agent(name=name, needs=needs, run=run)


def test_agents_run_in_parallel_and_the_supervisor_uses_no_model(doyle):
    plan = [slow("a", "summary"), slow("b", "coding.suggest"), slow("c", "priorauth.draft")]
    t0 = time.perf_counter()
    r = discharge.prepare(doyle, Gateway(None), WORKER, None, agents=plan)
    assert time.perf_counter() - t0 < 0.9                       # three 0.4 s agents finish in about 0.4 s, not 1.2 s
    assert [a.status for a in r.agents] == ["ok", "ok", "ok"] and [a.agent for a in r.agents] == ["a", "b", "c"]   # stable order
    with db.system() as c:
        assert c.execute("SELECT count(*) n FROM llm_call").fetchone()["n"] == 0                              # routing is plain code: no model call to decide anything


def test_one_failing_or_hanging_agent_does_not_stop_the_others(doyle):
    plan = [slow("a", "summary"), slow("b", "coding.suggest", fail=True), slow("c", "priorauth.draft", seconds=2.0)]
    r = discharge.prepare(doyle, Gateway(None), WORKER, None, agents=plan, timeout=0.8)
    by = {a.agent: a for a in r.agents}
    assert by["a"].status == "ok" and by["b"].status == "failed" and "blew up" in by["b"].error and by["c"].status == "failed" and "timed out" in by["c"].error
    assert not r.all_ok


def test_each_agent_runs_only_if_the_role_may_do_its_job(doyle):
    nurse = Principal(user="nurse.kim", role="NURSE", units=["5S-ORTH"])
    coord = Principal(user="pa.hughes", role="PA_COORD", units=["ALL"])
    plan = [slow("summary", "summary", 0), slow("coding", "coding.suggest", 0), slow("prior-auth", "priorauth.draft", 0)]
    got = lambda p: {a.agent: a.status for a in discharge.prepare(doyle, Gateway(None), p, None, agents=plan).agents}   # noqa: E731
    assert got(nurse) == {"summary": "ok", "coding": "skipped", "prior-auth": "skipped"}
    assert got(coord) == {"summary": "ok", "coding": "skipped", "prior-auth": "ok"}
    assert got(WORKER) == {"summary": "ok", "coding": "ok", "prior-auth": "ok"}
    assert got(Principal(user="it.admin", role="IT_ADMIN", units=["ALL"])) == {"summary": "skipped", "coding": "skipped", "prior-auth": "skipped"}


def test_real_agents_assemble_the_packet_and_the_human_to_do_list(doyle, payer):
    r = discharge.prepare(doyle, Gateway(None), WORKER, priorauth_api.deps.get_payer())
    by = {a.agent: a for a in r.agents}
    assert by["summary"].status == "degraded" and by["coding"].status == "degraded" and by["prior-auth"].status == "ok"   # no model: labelled fallbacks, still useful
    assert len(by["prior-auth"].artifacts) == 2                                                                         # SNF + walker drafts
    assert any(b.startswith("Prior authorization SNF-ADM") for b in r.blockers) and any(b.startswith("Prior authorization E0143") for b in r.blockers)
    assert any("documentation query" in b for b in r.blockers) or by["coding"].detail
    # run again: nothing is duplicated
    again = discharge.prepare(doyle, Gateway(None), WORKER, priorauth_api.deps.get_payer())
    by2 = {a.agent: a for a in again.agents}
    assert by2["coding"].status == "nothing-to-do" and by2["prior-auth"].status == "nothing-to-do" and "already in progress" in by2["prior-auth"].detail
    with db.system() as c:
        assert c.execute("SELECT count(*) n FROM prior_auth").fetchone()["n"] == 2


def test_a_payer_outage_fails_only_the_prior_auth_agent_and_the_event_is_retried(rt, payer, clean_db, doyle, monkeypatch):
    dead = PayerClient(http=httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")))), base="http://payer")
    monkeypatch.setattr(priorauth_api.deps, "payer", dead)
    runtime.gateway = Gateway(None)
    r = discharge.prepare(doyle, runtime.gateway, WORKER, dead)
    by = {a.agent: a for a in r.agents}
    assert by["prior-auth"].status == "failed" and "unreachable" in by["prior-auth"].error and by["summary"].status == "degraded" and by["coding"].status == "degraded"
    with pytest.raises(RuntimeError) as e:                                        # the worker retries the whole event; finished work is not repeated
        events.on_discharge_advised({"patient_id": doyle.patient_id})
    assert "prior-auth" in str(e.value)


def test_endpoint_runs_only_the_agents_the_caller_may_run_and_remembers_the_result(rt, payer, clean_db, client, meditrack):
    runtime.gateway = Gateway(None)
    pid = pid_of(rt, DOYLE)
    nurse = bearer(user_token(meditrack, DOYLE, user="cm.kim")["access_token"])           # case manager (NURSE role) on 5S-ORTH
    r = client.post(f"/api/patients/{pid}/readiness", headers=nurse)
    assert r.status_code == 200, r.text
    assert {a["agent"]: a["status"] for a in r.json()["agents"]} == {"summary": "degraded", "coding": "skipped", "prior-auth": "skipped"}
    got = client.get(f"/api/patients/{pid}/readiness", headers=nurse).json()
    assert got["generated_at"] and len(got["agents"]) == 3
    doc = bearer(user_token(meditrack, DOYLE, user="attending.whitfield")["access_token"])
    full = client.post(f"/api/patients/{pid}/readiness", headers=doc).json()
    assert {a["agent"]: a["status"] for a in full["agents"]}["coding"] in ("degraded", "nothing-to-do")
    from copilot import audit
    assert any(x["action"] == "readiness.prepared" for x in audit.search(patient_id=pid))
