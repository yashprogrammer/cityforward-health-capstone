"""The mock payer's policy and API behaviour (what the AI prior-auth assistant will be graded against)."""
import pytest
from fastapi.testclient import TestClient

from mock_payer.app import API_KEY, app

H = {"X-Api-Key": API_KEY}


@pytest.fixture()
def payer(monkeypatch):
    monkeypatch.setattr("mock_payer.app.REVIEW_SECONDS", 0.3)
    c = TestClient(app)
    c.post("/admin/reset", headers=H)
    return c


def claim(code, plan="Summit Medicare Advantage", ident="C-1", dx=("J44.1", "J96.01"), info=(), served="2026-10-23"):
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": {
        "resourceType": "Claim", "status": "active", "use": "preauthorization", "identifier": [{"value": ident}], "patient": {"reference": "Patient/9"},
        "insurer": {"display": plan}, "item": [{"sequence": 1, "productOrService": {"coding": [{"system": "https://www.cms.gov/hcpcs", "code": code}]}, "servicedDate": served}],
        "diagnosis": [{"sequence": i + 1, "diagnosisCodeableConcept": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": d}]}} for i, d in enumerate(dx)],
        "supportingInfo": [dict(sequence=i + 1, category={"coding": [{"code": cid}]}, **fields) for i, (cid, fields) in enumerate(info)]}}]}


def result(r):
    assert r.status_code == 200, r.text
    cr = r.json()["entry"][0]["resource"]
    action = cr["item"][0]["extension"][0]["valueCodeableConcept"]["coding"][0]["code"]
    return cr, action


OXYGEN = [("spo2-qualifying", {"valueQuantity": {"value": 87, "unit": "%"}, "timingDate": "2026-10-22"}),
          ("face-to-face", {"valueString": "Face-to-face evaluation by Dr. Okafor on 10/22/2026 documented hypoxemia on room air."}),
          ("therapy-optimized", {"valueBoolean": True})]


def test_catalogue_and_pa_requirement_by_plan(payer):
    assert {s["code"] for s in payer.get("/rules").json()["service"]} == {"E1390", "E0143", "SNF-ADM", "G0299"}
    assert payer.get("/rules/E1390", params={"plan": "Summit Medicare Advantage"}).json()["priorAuthRequired"] is True
    assert payer.get("/rules/E1390", params={"plan": "Medicare"}).json()["priorAuthRequired"] is False
    assert payer.get("/rules/E1390", params={"plan": "Medicaid"}).json()["priorAuthRequired"] is False
    assert payer.get("/rules/ZZZ").status_code == 404


def test_complete_request_is_certified_with_an_authorization_number(payer):
    cr, action = result(payer.post("/fhir/Claim/$submit", json=claim("E1390", info=OXYGEN), headers=H))
    assert action == "A1" and cr["outcome"] == "complete" and cr["preAuthRef"].startswith("PA-") and cr["preAuthPeriod"]["end"]


def test_missing_documentation_pends_and_lists_exactly_what_is_missing(payer):
    cr, action = result(payer.post("/fhir/Claim/$submit", json=claim("E1390", info=OXYGEN[:1]), headers=H))
    assert action == "A4" and cr["outcome"] == "queued"
    notes = " ".join(n["text"] for n in cr["processNote"])
    assert "face-to-face" in notes and "therapy-optimized" in notes and "spo2-qualifying" not in notes


def test_resubmission_with_the_missing_items_is_certified(payer):
    payer.post("/fhir/Claim/$submit", json=claim("E1390", ident="C-7", info=OXYGEN[:1]), headers=H)
    cr, action = result(payer.post("/fhir/Claim/$submit", json=claim("E1390", ident="C-7", info=OXYGEN), headers=H))
    assert action == "A1" and cr["meta"]["versionId"] == "2"
    assert payer.get(f"/fhir/ClaimResponse/{cr['id']}").json()["preAuthRef"] == cr["preAuthRef"]


def test_measurement_outside_policy_is_a_clinical_denial_not_a_pend(payer):
    bad = [("spo2-qualifying", {"valueQuantity": {"value": 93, "unit": "%"}, "timingDate": "2026-10-22"})] + OXYGEN[1:]
    cr, action = result(payer.post("/fhir/Claim/$submit", json=claim("E1390", info=bad), headers=H))
    assert action == "A3" and "93%" in cr["processNote"][0]["text"] and "appeal" in cr["processNote"][-1]["text"]


def test_stale_measurement_is_asked_for_again(payer):
    old = [("spo2-qualifying", {"valueQuantity": {"value": 86, "unit": "%"}, "timingDate": "2026-10-15"})] + OXYGEN[1:]
    cr, action = result(payer.post("/fhir/Claim/$submit", json=claim("E1390", info=old), headers=H))
    assert action == "A4" and "days old" in cr["processNote"][0]["text"]


def test_wrong_diagnosis_pends(payer):
    cr, action = result(payer.post("/fhir/Claim/$submit", json=claim("E1390", dx=("I10",), info=OXYGEN), headers=H))
    assert action == "A4" and "dx-chronic-resp" in cr["processNote"][0]["text"]


def test_traditional_medicare_needs_no_prior_auth(payer):
    cr, action = result(payer.post("/fhir/Claim/$submit", json=claim("E1390", plan="Medicare", info=[]), headers=H))
    assert action == "NA" and "not required" in cr["disposition"]


SNF = [("qualifying-stay", {"valueQuantity": {"value": 4, "unit": "midnights"}}),
       ("skilled-need", {"valueString": "Needs daily skilled PT and OT, hands-on help with transfers and stairs."}),
       ("functional-status", {"valueString": "Walks 40 feet with rolling walker and assistance of one; unable to climb stairs."}),
       ("discharge-plan", {"valueString": "Maple Grove Rehabilitation accepted; expected stay 14 days."})]


def test_snf_goes_through_clinical_review_and_must_be_polled(payer):
    cr, action = result(payer.post("/fhir/Claim/$submit", json=claim("SNF-ADM", plan="Lakeshore Medicare Advantage", ident="S-1", dx=("S72.001A",), info=SNF), headers=H))
    assert action == "A4" and cr["outcome"] == "queued" and "review in progress" in cr["disposition"]
    import time
    time.sleep(0.4)
    final = payer.get(f"/fhir/ClaimResponse/{cr['id']}").json()
    assert final["item"][0]["extension"][0]["valueCodeableConcept"]["coding"][0]["code"] == "A1" and final["preAuthRef"]


def test_snf_with_a_short_stay_is_denied_after_review(payer):
    short = [("qualifying-stay", {"valueQuantity": {"value": 1, "unit": "midnights"}})] + SNF[1:]
    cr, _ = result(payer.post("/fhir/Claim/$submit", json=claim("SNF-ADM", plan="Lakeshore Medicare Advantage", ident="S-2", dx=("S72.001A",), info=short), headers=H))
    import time
    time.sleep(0.4)
    final = payer.get("/fhir/ClaimResponse", params={"request": "S-2"}).json()["entry"][0]["resource"]
    assert final["item"][0]["extension"][0]["valueCodeableConcept"]["coding"][0]["code"] == "A3"


def test_request_validation_and_auth(payer):
    assert payer.post("/fhir/Claim/$submit", json=claim("E1390")).status_code == 401
    assert payer.post("/fhir/Claim/$submit", json={"resourceType": "Bundle", "entry": []}, headers=H).status_code == 400
    assert payer.post("/fhir/Claim/$submit", json=claim("99999"), headers=H).status_code == 422
    b = claim("E1390"); b["entry"][0]["resource"]["use"] = "claim"
    assert payer.post("/fhir/Claim/$submit", json=b, headers=H).status_code == 422
