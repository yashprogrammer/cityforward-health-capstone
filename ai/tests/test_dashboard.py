import pytest

from copilot import dashboard
from copilot.app import rt as runtime
from copilot.chart import load_chart
from copilot.gateway import Gateway
from tests.conftest import bearer, user_token

HALE, DOYLE, WHITAKER = "IP2610-0142", "IP2610-0151", "IP2610-0138"


def chart_of(rt, ip):
    return load_chart(rt.fhir, rt.fhir.read("Encounter", ip)["subject"]["reference"].split("/")[1])


def test_hale_flags_are_the_classic_payer_queries_and_each_shows_its_rule_and_evidence(rt):
    flags = {f.id.split(":")[0]: f for f in dashboard.flags_for(chart_of(rt, HALE))}
    assert "pending-result" in flags and "Lipoprotein" in flags["pending-result"].title and "discharge is being planned" in flags["pending-result"].title
    assert "implant-record-missing" in flags and "DEVICE_LOG" in flags["implant-record-missing"].rule
    assert "provisional-dx" in flags
    assert all(f.rule and f.evidence and f.evidence[0].ref for f in flags.values())
    assert flags["pending-result"].level == "attention"
    assert "operative-report-missing" not in flags                       # a cath report is filed


def test_other_patients_get_different_flags(rt):
    doyle = {f.id.split(":")[0] for f in dashboard.flags_for(chart_of(rt, DOYLE))}
    whit = {f.id.split(":")[0] for f in dashboard.flags_for(chart_of(rt, WHITAKER))}
    assert "post-acute-service" in doyle and "post-acute-service" in whit
    assert "implant-record-missing" not in doyle and "pending-result" not in doyle


def test_no_flag_is_a_prediction(rt):
    for ip in (HALE, DOYLE, WHITAKER):
        for f in dashboard.flags_for(chart_of(rt, ip)):
            from copilot import cds
            assert not cds.lint(f.title + " " + f.rule + " " + f.next_step), f


def test_timeline_is_chronological_and_each_event_links_to_a_fhir_resource(rt):
    tl = dashboard.timeline_for(chart_of(rt, HALE))
    assert [e.when for e in tl] == sorted(e.when for e in tl)
    kinds = {e.kind for e in tl}
    assert {"encounter", "procedure", "lab", "note"} <= kinds
    assert all("/" in e.ref for e in tl)
    assert any(e.kind == "lab" and e.flag == "pending" for e in tl)


def test_dashboard_endpoint_for_the_launched_clinician(rt, clean_db, client, meditrack):
    pid = rt.fhir.read("Encounter", HALE)["subject"]["reference"].split("/")[1]
    tok = bearer(user_token(meditrack, HALE)["access_token"])
    r = client.get(f"/api/patients/{pid}/dashboard", headers=tok)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["patient"]["name"] == "Robert Hale" and d["patient"]["unit"] == "4W-CARD" and d["patient"]["los_days"] >= 4
    assert d["flags"] and d["timeline"] and d["cds"]["for"] == "licensed health care professionals"
    # opening the dashboard indexed the chart, so the other tabs work straight away
    runtime.gateway = Gateway(None)
    assert client.post(f"/api/patients/{pid}/ask", headers=tok, json={"question": "aspirin"}).status_code == 200
    other = bearer(user_token(meditrack, HALE, user="nurse.park")["access_token"])
    assert client.get(f"/api/patients/{pid}/dashboard", headers=other).status_code == 403
