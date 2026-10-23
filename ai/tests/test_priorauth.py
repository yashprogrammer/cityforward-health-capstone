import time

import pytest

from copilot import db, priorauth
from copilot.app import rt as runtime
from copilot.chart import load_chart
from copilot.gateway import Gateway
from tests.conftest import RuleBasedLLM, bearer, user_token

DOYLE, WHITAKER, HALE = "IP2610-0151", "IP2610-0138", "IP2610-0142"

O2_MAP = {"spo2-qualifying": (r"resting SpO2 87%", r"resting SpO2 (\d+)%"), "face-to-face": (r"face-to-face evaluation", None),
          "therapy-optimized": (r"optimi[sz]ed on", None)}
SNF_MAP = {"skilled-need": (r"skilled physical and occupational therapy daily", None), "functional-status": (r"ambulated 40 feet", None),
           "discharge-plan": (r"Maple Grove", None)}


def pid_of(rt, ip):
    return rt.fhir.read("Encounter", ip)["subject"]["reference"].split("/")[1]


def tok(meditrack, ip, user):
    return bearer(user_token(meditrack, ip, user=user)["access_token"])


@pytest.fixture()
def env(rt, payer, clean_db, meditrack):
    from copilot import rag
    for ip in (DOYLE, WHITAKER, HALE):
        rag.index_chart(load_chart(rt.fhir, pid_of(rt, ip)))
    return rt


def test_suggestions_come_from_the_chart_and_the_plan(env, client, meditrack):
    r = client.get(f"/api/patients/{pid_of(env, DOYLE)}/prior-auth/suggestions", headers=tok(meditrack, DOYLE, "cm.kim"))
    assert r.status_code == 200 and {s["service_code"] for s in r.json()} == {"SNF-ADM", "E0143"} and all(s["pa_required"] for s in r.json())
    r = client.get(f"/api/patients/{pid_of(env, WHITAKER)}/prior-auth/suggestions", headers=tok(meditrack, WHITAKER, "pa.hughes"))
    assert [s["service_code"] for s in r.json()] == ["E1390"]
    r = client.get(f"/api/patients/{pid_of(env, HALE)}/prior-auth/suggestions", headers=tok(meditrack, HALE, "pa.hughes"))
    assert r.json() == []        # a heart-attack stay with no post-acute service: nothing to authorize


def test_home_oxygen_end_to_end_with_human_in_the_loop(env, client, meditrack, payer):
    pid = pid_of(env, WHITAKER)
    runtime.gateway = Gateway(RuleBasedLLM(O2_MAP))
    coord, doc = tok(meditrack, WHITAKER, "pa.hughes"), tok(meditrack, WHITAKER, "attending.okafor")
    assert client.post(f"/api/patients/{pid}/prior-auth", headers=tok(meditrack, WHITAKER, "nurse.park"), json={"service_code": "E1390"}).status_code == 403
    r = client.post(f"/api/patients/{pid}/prior-auth", headers=coord, json={"service_code": "E1390"})
    assert r.status_code == 201, r.text
    pa = r.json()
    by = {q["id"]: q for q in pa["requirements"]}
    assert pa["mode"] == "llm" and pa["status"] == "DRAFT"
    assert by["dx-chronic-resp"]["status"] == "met" and "J44" in by["dx-chronic-resp"]["statement"]       # deterministic, from the problem list
    assert by["spo2-qualifying"]["status"] == "met" and by["spo2-qualifying"]["value"] == 87.0             # model found it in the progress note
    assert by["face-to-face"]["status"] == "met"
    assert by["therapy-optimized"]["status"] == "needs_attestation"                                         # never auto-attested
    assert pa["gaps"] == ["therapy-optimized"] and not pa["can_approve"]
    pa_id = pa["id"]
    assert client.post(f"/api/prior-auth/{pa_id}/approve", headers=doc).status_code == 409                  # gap remains
    assert client.post(f"/api/prior-auth/{pa_id}/requirements/therapy-optimized", headers=coord, json={"action": "attest"}).status_code == 403   # coordinator cannot attest
    assert client.post(f"/api/prior-auth/{pa_id}/requirements/therapy-optimized", headers=doc, json={"action": "attest"}).status_code == 200
    assert client.post(f"/api/prior-auth/{pa_id}/submit", headers=coord).status_code == 409                # not approved yet
    assert client.post(f"/api/prior-auth/{pa_id}/approve", headers=coord).status_code == 403                # only a clinician signs
    assert client.post(f"/api/prior-auth/{pa_id}/approve", headers=doc).json()["status"] == "APPROVED"
    done = client.post(f"/api/prior-auth/{pa_id}/submit", headers=coord).json()
    assert done["status"] == "CERTIFIED" and done["payer_action"] == "A1" and done["preauth_ref"].startswith("PA-")
    kinds = [h["what"] for h in done["history"]]
    assert any("approved" in k for k in kinds) and any("attest" in k for k in kinds)
    docs = env.fhir.search("DocumentReference", patient=pid)
    assert any((d.get("type") or {}).get("text") == "PRIOR_AUTH" or "PRIOR_AUTH" in str(d.get("type")) for d in docs)   # filed back into the chart


def test_a_value_that_does_not_meet_policy_is_flagged_before_the_payer_denies_it(env, client, meditrack):
    pid = pid_of(env, WHITAKER)
    mapping = {**O2_MAP, "spo2-qualifying": (r"Resting SpO2 91% on room air", r"SpO2 (\d+)%")}     # the old clinic reading
    runtime.gateway = Gateway(RuleBasedLLM(mapping))
    pa = client.post(f"/api/patients/{pid}/prior-auth", headers=tok(meditrack, WHITAKER, "pa.hughes"), json={"service_code": "E1390"}).json()
    q = {x["id"]: x for x in pa["requirements"]}["spo2-qualifying"]
    assert q["status"] == "fails_policy" and "deny" in q["note"]
    r = client.post(f"/api/prior-auth/{pa['id']}/requirements/spo2-qualifying", headers=tok(meditrack, WHITAKER, "attending.okafor"), json={"action": "accept", "statement": "x" * 30})
    assert r.status_code == 409


def test_a_fabricated_number_never_reaches_the_draft(env, client, meditrack):
    pid = pid_of(env, WHITAKER)
    lying = RuleBasedLLM(O2_MAP, tamper={"spo2-qualifying": {"value": 80.0}})       # claims 80%: not in the passage
    runtime.gateway = Gateway(lying)
    pa = client.post(f"/api/patients/{pid}/prior-auth", headers=tok(meditrack, WHITAKER, "pa.hughes"), json={"service_code": "E1390"}).json()
    q = {x["id"]: x for x in pa["requirements"]}["spo2-qualifying"]
    assert q["status"] == "missing" and q["value"] is None and any("does not appear" in p for p in pa["problems"])
    assert len(lying.prompts) == 2 and "does not appear" in lying.prompts[1][1]     # it was sent back once with the reason


def test_offline_mode_gives_candidates_a_human_must_accept(env, client, meditrack):
    pid = pid_of(env, WHITAKER)
    runtime.gateway = Gateway(None)
    coord, doc = tok(meditrack, WHITAKER, "pa.hughes"), tok(meditrack, WHITAKER, "attending.okafor")
    pa = client.post(f"/api/patients/{pid}/prior-auth", headers=coord, json={"service_code": "E1390"}).json()
    assert pa["mode"] == "deterministic"
    by = {q["id"]: q for q in pa["requirements"]}
    assert by["dx-chronic-resp"]["status"] == "met"
    cand = [q for q in pa["requirements"] if q["status"] == "candidate"]
    assert cand and all(q["evidence"] for q in cand)
    r = client.post(f"/api/prior-auth/{pa['id']}/requirements/{cand[0]['id']}", headers=coord, json={"action": "accept"})
    assert r.status_code == 422                                  # you must write what you are accepting
    r = client.post(f"/api/prior-auth/{pa['id']}/requirements/{cand[0]['id']}", headers=coord, json={"action": "accept", "statement": "Reviewed the cited note."})
    assert {q["id"]: q for q in r.json()["requirements"]}[cand[0]["id"]]["status"] == "accepted"


def test_incomplete_request_pends_and_the_loop_closes_after_the_clinician_adds_the_evidence(env, client, meditrack):
    pid = pid_of(env, WHITAKER)
    runtime.gateway = Gateway(None)                              # nothing verified: every document requirement is open
    coord, doc = tok(meditrack, WHITAKER, "pa.hughes"), tok(meditrack, WHITAKER, "attending.okafor")
    pa = client.post(f"/api/patients/{pid}/prior-auth", headers=coord, json={"service_code": "E1390"}).json()
    assert client.post(f"/api/prior-auth/{pa['id']}/approve", headers=doc).status_code == 409
    r = client.post(f"/api/prior-auth/{pa['id']}/approve", headers=doc, json={"submit_with_gaps_reason": "Payer wants it today; will add the rest on request."})
    assert r.json()["status"] == "APPROVED"
    sub = client.post(f"/api/prior-auth/{pa['id']}/submit", headers=coord).json()
    assert sub["status"] == "PENDED" and sub["payer_action"] == "A4" and any(n.startswith("Missing:") for n in sub["payer_notes"])
    for rid, extra in (("spo2-qualifying", {"value": 87, "measured_on": "2026-10-21"}), ("face-to-face", {}), ("therapy-optimized", {})):
        act = "attest" if rid == "therapy-optimized" else "provide"
        body = {"action": act, "statement": "Resting SpO2 87% on room air on 2026-10-21 (progress note, Dr. Okafor).", **extra}
        assert client.post(f"/api/prior-auth/{pa['id']}/requirements/{rid}", headers=doc, json=body).status_code == 200
    got = client.get(f"/api/prior-auth/{pa['id']}", headers=doc).json()
    assert got["status"] == "DRAFT" and got["gaps"] == []          # editing reopened it for sign-off
    assert client.post(f"/api/prior-auth/{pa['id']}/approve", headers=doc).json()["status"] == "APPROVED"
    final = client.post(f"/api/prior-auth/{pa['id']}/submit", headers=coord).json()
    assert final["status"] == "CERTIFIED" and final["claim_identifier"] == pa["id"]       # same identifier: the payer re-adjudicated, not a new case


def test_snf_manual_review_is_queued_then_certified(env, client, meditrack, payer):
    pid = pid_of(env, DOYLE)
    runtime.gateway = Gateway(RuleBasedLLM(SNF_MAP))
    coord, doc = tok(meditrack, DOYLE, "pa.hughes"), tok(meditrack, DOYLE, "attending.whitfield")
    pa = client.post(f"/api/patients/{pid}/prior-auth", headers=coord, json={"service_code": "SNF-ADM"}).json()
    by = {q["id"]: q for q in pa["requirements"]}
    assert by["qualifying-stay"]["status"] == "met" and by["qualifying-stay"]["value"] >= 3          # computed from the encounter, not by the model
    assert pa["gaps"] == [], by
    client.post(f"/api/prior-auth/{pa['id']}/approve", headers=doc)
    sub = client.post(f"/api/prior-auth/{pa['id']}/submit", headers=coord).json()
    assert sub["status"] == "QUEUED"
    time.sleep(0.5)
    assert client.post(f"/api/prior-auth/{pa['id']}/refresh", headers=coord).json()["status"] == "CERTIFIED"


def test_it_admin_sees_no_patient_data(env, client, meditrack):
    pid = pid_of(env, WHITAKER)
    assert client.get(f"/api/patients/{pid}/prior-auth", headers=tok(meditrack, WHITAKER, "it.admin")).status_code == 403


def test_payer_outage_is_a_clear_502_not_a_crash(env, client, meditrack, monkeypatch):
    import httpx
    from copilot import priorauth_api
    from copilot.payer_client import PayerClient
    pid = pid_of(env, WHITAKER)
    dead = PayerClient(http=httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")))), base="http://payer")
    monkeypatch.setattr(priorauth_api.deps, "payer", dead)
    r = client.get(f"/api/patients/{pid}/prior-auth/suggestions", headers=tok(meditrack, WHITAKER, "pa.hughes"))
    assert r.status_code == 502 and "unreachable" in r.json()["detail"]
    r = client.post(f"/api/patients/{pid}/prior-auth", headers=tok(meditrack, WHITAKER, "pa.hughes"), json={"service_code": "E1390"})
    assert r.status_code == 502
