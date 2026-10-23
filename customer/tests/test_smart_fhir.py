"""SMART on FHIR (EHR launch + backend services) and the FHIR R4 API."""
import base64
import pytest

from conftest import APP_REDIRECT, FHIR, backend_assertion, backend_token, bearer, ehr_launch_token


@pytest.fixture(autouse=True, scope="module")
def pristine(seeded):
    from meditrack.backend.seed import seed
    seed()
    yield


def test_smart_configuration_advertises_launch_and_pkce(client):
    cfg = client.get("/fhir/.well-known/smart-configuration").json()
    assert "launch-ehr" in cfg["capabilities"] and cfg["code_challenge_methods_supported"] == ["S256"]
    assert cfg["token_endpoint"].endswith("/smart/token") and client.get("/smart/jwks.json").json()["keys"][0]["alg"] == "RS256"
    cap = client.get("/fhir/metadata").json()
    assert cap["fhirVersion"] == "4.0.1" and {r["type"] for r in cap["rest"][0]["resource"]} >= {"Patient", "Observation", "DocumentReference"}


def test_fhir_requires_a_token(client):
    r = client.get("/fhir/Patient/1")
    assert r.status_code == 401 and r.json()["resourceType"] == "OperationOutcome"
    assert client.get("/fhir/Patient/1", headers=bearer("not-a-jwt")).status_code == 401


def test_ehr_launch_gives_patient_and_encounter_context(client, smart_link):
    r = ehr_launch_token(client, smart_link, "IP2610-0142", user="attending.raman")
    assert r.status_code == 200, r.text
    tok = r.json()
    assert tok["encounter"] == "IP2610-0142" and tok["need_patient_banner"] and tok["id_token"]
    patient = client.get(f"/fhir/Patient/{tok['patient']}", headers=bearer(tok["access_token"])).json()
    assert patient["name"][0]["family"] == "Hale" and "ssn" not in str(patient).lower()
    claims = __import__("jwt").decode(tok["access_token"], options={"verify_signature": False})
    assert claims["role"] == "ATTENDING" and claims["unit"] == "4W-CARD" and claims["sub"] == "attending.raman"


def test_patient_token_cannot_read_another_patient(client, smart_link):
    tok = ehr_launch_token(client, smart_link, "IP2610-0142").json()
    other = client.get("/fhir/Encounter/IP2610-0151", headers=bearer(tok["access_token"]))
    assert other.status_code == 403
    mine = client.get("/fhir/Encounter", params={"patient": tok["patient"]}, headers=bearer(tok["access_token"])).json()
    assert mine["total"] == 3 and all(e["resource"]["subject"]["reference"] == f"Patient/{tok['patient']}" for e in mine["entry"])
    # searching without a patient defaults to the token's own patient, never everyone
    default = client.get("/fhir/Observation", headers=bearer(tok["access_token"])).json()
    assert default["total"] > 0 and {e["resource"]["subject"]["reference"] for e in default["entry"]} == {f"Patient/{tok['patient']}"}
    assert client.get("/fhir/Patient", headers=bearer(tok["access_token"])).json()["total"] == 1


def test_pkce_code_reuse_redirect_and_scope_are_enforced(client, smart_link):
    assert ehr_launch_token(client, smart_link, "IP2610-0142", verifier_override="wrong-verifier").status_code == 400
    r = client.get("/smart/authorize", params={"response_type": "code", "client_id": "cityforward-ai-dashboard", "redirect_uri": "http://evil.example/cb",
                                               "scope": "launch patient/*.read", "aud": FHIR, "launch": "x", "code_challenge": "c", "code_challenge_method": "S256"},
                   follow_redirects=False)
    assert r.status_code == 400 and "redirect_uri" in r.text   # never bounce a code to an unregistered URI
    bad_scope = ehr_launch_token_expect_error(client, smart_link, "launch system/*.read")
    assert "invalid_scope" in bad_scope


def ehr_launch_token_expect_error(client, smart_link, scope):
    from urllib.parse import parse_qs, urlparse
    r = client.get(f"/smart/ehr-launch?lnk_id={smart_link}&ip_no=IP2610-0142", follow_redirects=False)
    launch = parse_qs(urlparse(r.headers["location"]).query)["launch"][0]
    r = client.get("/smart/authorize", params={"response_type": "code", "client_id": "cityforward-ai-dashboard", "redirect_uri": APP_REDIRECT, "scope": scope,
                                               "aud": FHIR, "launch": launch, "code_challenge": "c", "code_challenge_method": "S256"}, follow_redirects=False)
    return r.headers["location"]


def test_authorization_code_is_single_use(client, smart_link):
    from urllib.parse import parse_qs, urlparse
    import conftest
    r = client.get(f"/smart/ehr-launch?lnk_id={smart_link}&ip_no=IP2610-0142", follow_redirects=False)
    launch = parse_qs(urlparse(r.headers["location"]).query)["launch"][0]
    verifier, challenge = conftest._pkce()
    a = client.get("/smart/authorize", params={"response_type": "code", "client_id": "cityforward-ai-dashboard", "redirect_uri": APP_REDIRECT, "scope": "launch patient/*.read",
                                               "aud": FHIR, "launch": launch, "code_challenge": challenge, "code_challenge_method": "S256"}, follow_redirects=False)
    code = parse_qs(urlparse(a.headers["location"]).query)["code"][0]
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": APP_REDIRECT, "client_id": "cityforward-ai-dashboard", "code_verifier": verifier}
    assert client.post("/smart/token", data=data).status_code == 200
    assert client.post("/smart/token", data=data).status_code == 400


def test_backend_services_flow_and_assertion_checks(client, backend_key):
    r = backend_token(client, backend_key)
    assert r.status_code == 200 and r.json()["expires_in"] == 300
    tok = r.json()["access_token"]
    inpatients = client.get("/fhir/Encounter", params={"status": "in-progress", "class": "IMP"}, headers=bearer(tok)).json()
    assert inpatients["total"] == 11
    # replay, wrong audience, and scopes beyond the registration are refused
    key = backend_key
    one = backend_assertion(key)
    data = {"grant_type": "client_credentials", "scope": "system/*.read", "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer", "client_assertion": one}
    assert client.post("/smart/token", data=data).status_code == 200
    assert client.post("/smart/token", data=data).status_code == 401
    assert backend_token(client, key, aud="http://elsewhere/token").status_code == 401
    assert backend_token(client, key, scope="system/Patient.write").status_code == 400
    assert backend_token(client, key, scope="user/*.read").status_code == 400
    assert backend_token(client, key, ttl=3600).status_code == 401


def test_fhir_resource_content(client, backend_key):
    tok = backend_token(client, backend_key).json()["access_token"]
    h = bearer(tok)
    pid = client.get("/fhir/Patient", params={"identifier": "CF306377"}, headers=h).json()["entry"][0]["resource"]["id"]
    enc = client.get("/fhir/Encounter/IP2610-0142", headers=h).json()
    assert enc["class"]["code"] == "IMP" and enc["status"] == "in-progress" and enc["location"][0]["location"]["display"].startswith("4 West")
    dx = client.get("/fhir/Condition", params={"patient": pid}, headers=h).json()["entry"]
    assert any(e["resource"]["code"]["coding"][0] == {"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "I21.02", "display": e["resource"]["code"]["text"]} for e in dx)
    vitals = client.get("/fhir/Observation", params={"patient": pid, "category": "vital-signs"}, headers=h).json()
    labs = client.get("/fhir/Observation", params={"patient": pid, "category": "laboratory"}, headers=h).json()
    assert vitals["total"] > 10 and labs["total"] > 8
    trop = client.get("/fhir/Observation", params={"patient": pid, "code": "89579-7", "_sort": "date"}, headers=h).json()["entry"]
    assert [e["resource"]["valueQuantity"]["value"] for e in trop] == [1840.0, 14250.0, 3100.0]
    assert trop[0]["resource"]["interpretation"][0]["coding"][0]["code"] == "H"
    pending = [e["resource"] for e in client.get("/fhir/Observation", params={"patient": pid, "code": "10835-7"}, headers=h).json()["entry"]]
    assert pending and pending[0]["status"] == "registered" and "valueQuantity" not in pending[0]
    meds = client.get("/fhir/MedicationRequest", params={"patient": pid, "category": "inpatient"}, headers=h).json()["entry"]
    assert any("ticagrelor" in e["resource"]["medicationCodeableConcept"]["text"] for e in meds)
    notes = client.get("/fhir/DocumentReference", params={"patient": pid, "encounter": "IP2610-0142"}, headers=h).json()["entry"]
    note = next(e["resource"] for e in notes if e["resource"]["type"]["coding"][0]["code"] == "11506-3")
    binary = client.get(note["content"][0]["attachment"]["url"].replace("http://testserver", ""), headers=h)
    assert binary.status_code == 200 and "chest pain" in binary.text.lower()
    cov = client.get("/fhir/Coverage", params={"patient": pid}, headers=h).json()["entry"][0]["resource"]
    assert cov["payor"][0]["display"] == "Summit Health Plan" and cov["subscriberId"].startswith("W")
    everything = client.get(f"/fhir/Patient/{pid}/$everything", headers=h).json()
    types = {e["resource"]["resourceType"] for e in everything["entry"]}
    assert types >= {"Patient", "Encounter", "Condition", "Observation", "MedicationRequest", "DocumentReference", "Procedure"}
    assert client.get("/fhir/Practitioner/D9999900001", headers=h).json()["identifier"][0]["value"] == "9999900001"
    assert client.get("/fhir/Observation/obs-99999999", headers=h).status_code == 404


def test_document_write_back(client, backend_key):
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
    pid = client.get("/int/ip/IP2610-0151").json()["adm"]["PT_ID"]
    body = {"resourceType": "DocumentReference", "status": "current", "type": {"coding": [{"code": "DSCHSUMM"}]},
            "subject": {"reference": f"Patient/{pid}"}, "context": {"encounter": [{"reference": "Encounter/IP2610-0151"}]},
            "content": [{"attachment": {"contentType": "application/pdf", "data": base64.b64encode(pdf).decode()}}]}
    read_only = backend_token(client, backend_key, scope="system/*.read").json()["access_token"]
    assert client.post("/fhir/DocumentReference", json=body, headers=bearer(read_only)).status_code == 403
    tok = backend_token(client, backend_key, scope="system/*.read system/DocumentReference.write").json()["access_token"]
    r = client.post("/fhir/DocumentReference", json=body, headers=bearer(tok))
    assert r.status_code == 201 and r.json()["context"]["encounter"][0]["reference"] == "Encounter/IP2610-0151"
    docs = client.get("/int/ip/IP2610-0151").json()["docs"]
    assert any(d["DOC_TYP"] == "DSCHSUMM" and d["UPL_BY"] == "test-backend" for d in docs)   # visible in MediTrack's Documents tab
    pdf_back = client.get(r.json()["content"][0]["attachment"]["url"].replace("http://testserver", ""), headers=bearer(tok))
    assert pdf_back.content == pdf
    # guards: not a PDF, wrong patient for the encounter, bad base64
    bad = dict(body, content=[{"attachment": {"contentType": "application/pdf", "data": base64.b64encode(b"hello").decode()}}])
    assert client.post("/fhir/DocumentReference", json=bad, headers=bearer(tok)).status_code == 422
    wrong = dict(body, subject={"reference": "Patient/1"})
    assert client.post("/fhir/DocumentReference", json=wrong, headers=bearer(tok)).status_code == 422
    nob64 = dict(body, content=[{"attachment": {"contentType": "application/pdf", "data": "###"}}])
    assert client.post("/fhir/DocumentReference", json=nob64, headers=bearer(tok)).status_code == 400


def test_fhir_reads_are_audited(client, backend_key):
    tok = backend_token(client, backend_key).json()["access_token"]
    client.get("/fhir/Encounter/IP2610-0138", headers=bearer(tok))
    audit = client.get("/int/audit").json()
    assert any(a["ACTN"] == "FHIR_READ:Encounter" and a["REF"] == "IP2610-0138" and a["USR"] == "test-backend" for a in audit)
