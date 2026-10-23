import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from copilot import audit, db, rag
from copilot.app import rt as runtime
from copilot.auth import Issuer, Principal, SYSTEM, Verifier
from copilot.chart import load_chart
from copilot.gateway import Gateway
from tests.conftest import bearer, user_token

HALE, WHITAKER = "IP2610-0142", "IP2610-0138"     # Hale: 4W-CARD (cardiology). Whitaker: 3E-MED.


def pid_of(rt, ip):
    return rt.fhir.read("Encounter", ip)["subject"]["reference"].split("/")[1]


def P(user, role, units, patients=()):
    return Principal(user=user, role=role, units=list(units), patients=list(patients))


@pytest.fixture()
def two_units(rt, clean_db):
    ids = {}
    for ip in (HALE, WHITAKER):
        pid = pid_of(rt, ip)
        rag.index_chart(load_chart(rt.fhir, pid))
        ids[ip] = pid
    return ids


def count(principal, table="chunk", where="TRUE"):
    with db.session(principal) as c:
        return c.execute(f"SELECT count(*) n FROM {table} WHERE {where}").fetchone()["n"]


def test_rls_hides_other_units_even_when_application_code_forgets_to_check(two_units):
    hale, whit = two_units[HALE], two_units[WHITAKER]
    nurse = P("nurse.park", "NURSE", ["3E-MED"])
    assert rag.search(hale, "aspirin", k=5, principal=nurse) == []              # no authorize() anywhere in this call: the database says no
    assert rag.search(whit, "oxygen", k=5, principal=nurse)
    assert count(nurse, where=f"patient_id='{hale}'") == 0 and count(nurse, where=f"patient_id='{whit}'") > 0
    assert count(nurse, "patient_index") == 1


def test_missing_principal_fails_closed(two_units):
    assert count(None) == 0 and count(None, "patient_index") == 0 and count(None, "prior_auth") == 0


def test_roles_without_phi_access_see_nothing_even_with_all_units(two_units):
    assert count(P("it.admin", "IT_ADMIN", ["ALL"])) == 0
    assert count(P("privacy", "COMPLIANCE", ["ALL"])) == 0
    assert count(P("pa.hughes", "PA_COORD", ["ALL"])) > 0


def test_smart_launch_scope_limits_a_cross_unit_role_to_one_patient(two_units):
    hale, whit = two_units[HALE], two_units[WHITAKER]
    p = P("attending.x", "ATTENDING", ["ALL"], patients=[whit])
    assert count(p, where=f"patient_id='{hale}'") == 0 and count(p, where=f"patient_id='{whit}'") > 0


def test_cannot_write_rows_into_a_unit_you_cannot_see(two_units):
    nurse = P("nurse.park", "NURSE", ["3E-MED"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.session(nurse) as c:
            c.execute("INSERT INTO patient_index (patient_id, unit, chunks) VALUES ('999', '4W-CARD', 0)")


def test_break_glass_flow(two_units, client, meditrack):
    hale = two_units[HALE]
    runtime.gateway = Gateway(None)
    nurse = bearer(user_token(meditrack, HALE, user="nurse.park")["access_token"])       # 3E-MED nurse, patient on 4W-CARD
    body = {"question": "aspirin"}
    assert client.post(f"/api/patients/{hale}/ask", headers=nurse, json=body).status_code == 403
    assert client.post(f"/api/patients/{hale}/break-glass", headers=nurse, json={"reason": "urgent"}).status_code == 422
    g = client.post(f"/api/patients/{hale}/break-glass", headers=nurse, json={"reason": "Cross-cover for transferred patient with chest pain overnight"})
    assert g.status_code == 201 and "reviewed" in g.json()["notice"]
    assert client.post(f"/api/patients/{hale}/ask", headers=nurse, json=body).status_code == 200      # access granted at once
    assert rag.search(hale, "aspirin", k=3, principal=P("nurse.park", "NURSE", ["3E-MED"]))             # the database agrees (policy sees the grant)
    # the privacy officer's queue and the audit trail
    admin = bearer(user_token(meditrack, HALE, user="it.admin")["access_token"])
    queue = client.get("/api/break-glass", headers=admin).json()
    assert queue[0]["user_id"] == "nurse.park" and queue[0]["reviewed_at"] is None
    trail = client.get("/api/audit", headers=admin, params={"patient": hale}).json()
    assert any(r["action"] == "break-glass" for r in trail) and any(r["action"] == "ask" and r["detail"].get("break_glass") for r in trail)
    assert client.post(f"/api/break-glass/{queue[0]['id']}/review", headers=admin, json={"note": "Legitimate cross-cover, confirmed with charge nurse."}).status_code == 200
    assert client.get("/api/break-glass", headers=admin).json()[0]["reviewed_at"]
    # grants expire
    with db.system() as c:
        c.execute("UPDATE break_glass SET expires_at = now() - interval '1 minute'")
    assert client.post(f"/api/patients/{hale}/ask", headers=nurse, json=body).status_code == 403
    assert rag.search(hale, "aspirin", k=3, principal=P("nurse.park", "NURSE", ["3E-MED"])) == []


def test_break_glass_limits(two_units, client, meditrack):
    hale, whit = two_units[HALE], two_units[WHITAKER]
    reason = {"reason": "Cross-cover for transferred patient overnight"}
    pa = bearer(user_token(meditrack, HALE, user="pa.hughes")["access_token"])
    assert client.post(f"/api/patients/{hale}/break-glass", headers=pa, json=reason).status_code == 403          # coordinators have no emergency access
    cardio = bearer(user_token(meditrack, HALE, user="attending.raman")["access_token"])
    assert client.post(f"/api/patients/{hale}/break-glass", headers=cardio, json=reason).status_code == 409      # already their unit
    assert client.post(f"/api/patients/{whit}/break-glass", headers=cardio, json=reason).status_code == 403      # token launched on another patient
    admin = bearer(user_token(meditrack, HALE, user="it.admin")["access_token"])
    assert client.post(f"/api/patients/{hale}/break-glass", headers=admin, json=reason).status_code == 403


# ---------------------------------------------------------------------------------------------- Keycloak SSO tokens
@pytest.fixture(scope="module")
def keycloak():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="kc-1", alg="RS256", use="sig")
    http = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"keys": [jwk]})))
    iss = "https://idp.hospital.example/realms/cityforward"
    ver = Verifier(http=http, issuers=[Issuer(name="keycloak", iss=iss, jwks_url=iss + "/protocol/openid-connect/certs", kind="keycloak", audience="cityforward-ai")])

    def mint(claims, signer=key, kid="kc-1"):
        t = int(time.time())
        return jwt.encode({"iss": iss, "aud": "cityforward-ai", "iat": t, "exp": t + 300, "sub": "u-1", **claims}, signer, algorithm="RS256", headers={"kid": kid})
    return ver, mint


def test_keycloak_tokens_map_to_principals(keycloak):
    ver, mint = keycloak
    p = ver.verify(mint({"preferred_username": "dr.kim", "name": "Dr. Kim", "realm_access": {"roles": ["offline_access", "ATTENDING"]}, "units": ["4W-CARD", "3E-MED"]}))
    assert (p.user, p.role, p.units) == ("dr.kim", "ATTENDING", ["4W-CARD", "3E-MED"]) and p.patients == []
    assert ver.verify(mint({"realm_access": {"roles": ["COMPLIANCE"]}})).role == "COMPLIANCE"


def test_keycloak_rejections(keycloak):
    ver, mint = keycloak
    with pytest.raises(HTTPException) as e:
        ver.verify(mint({"realm_access": {"roles": ["offline_access"]}}))
    assert e.value.status_code == 403                                                          # authenticated but not a hospital role
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException) as e:
        ver.verify(mint({"realm_access": {"roles": ["NURSE"]}}, signer=other))
    assert e.value.status_code == 401                                                          # wrong signing key
    with pytest.raises(HTTPException) as e:
        ver.verify(mint({"aud": "someone-else", "realm_access": {"roles": ["NURSE"]}}))
    assert e.value.status_code == 401                                                          # token minted for another client
    with pytest.raises(HTTPException) as e:
        ver.verify(mint({"iss": "https://evil.example/realms/x", "realm_access": {"roles": ["NURSE"]}}))
    assert e.value.status_code == 401                                                          # untrusted issuer
