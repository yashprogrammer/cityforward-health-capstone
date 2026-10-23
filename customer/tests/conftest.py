import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("WEBHOOK_DISPATCHER", "off")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("MEDITRACK_DB_URL", "postgresql://meditrack:meditrack@localhost:5432/meditrack")
os.environ.setdefault("MEDITRACK_DOCS_DIR", str(Path(__file__).resolve().parent / ".data" / "documents"))
os.environ.setdefault("MEDITRACK_HOTFOLDER", str(Path(__file__).resolve().parent / ".data" / "hotfolder"))


@pytest.fixture(scope="session")
def seeded():
    """Reseed MediTrack once per test session (about 3 seconds)."""
    import psycopg
    try:
        psycopg.connect(os.environ["MEDITRACK_DB_URL"], connect_timeout=3).close()
    except psycopg.Error as e:   # pragma: no cover
        pytest.skip(f"PostgreSQL not reachable ({e}); run: docker compose up -d db && python -m meditrack.backend.bootstrap")
    from meditrack.backend.seed import seed
    return seed()


@pytest.fixture(scope="session")
def client(seeded):
    from fastapi.testclient import TestClient
    from meditrack.backend.app import app
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------- helpers for SMART flows
import base64
import hashlib
import secrets
import time
import uuid
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

APP_REDIRECT = "http://localhost:8001/ai/auth/callback"
FHIR = "http://testserver/fhir"
TOKEN_URL = "http://testserver/smart/token"


def _pkce():
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


@pytest.fixture(scope="session")
def smart_link(client):
    """Register a SMART link once (what IT does in System Admin > External Links)."""
    client.post("/int/admin/extlinks", json={"label": "AI Clinical Insights", "url": "http://localhost:8001/ai/launch", "mode": "SMART"})
    return next(l for l in client.get("/int/admin/extlinks").json() if l["DISP_MD"] == "SMART")["LNK_ID"]


def ehr_launch_token(client, smart_link, ip_no, scope="launch openid fhirUser patient/*.read user/Practitioner.read patient/DocumentReference.write",
                     user=None, verifier_override=None):
    """Run the whole SMART EHR launch like a browser would and return the token response."""
    cookies = {"mt_user": user} if user else {}
    r = client.get(f"/smart/ehr-launch?lnk_id={smart_link}&ip_no={ip_no}", follow_redirects=False, cookies=cookies)
    assert r.status_code == 302, r.text
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["iss"] == [FHIR]
    verifier, challenge = _pkce()
    r = client.get("/smart/authorize", params={"response_type": "code", "client_id": "cityforward-ai-dashboard", "redirect_uri": APP_REDIRECT,
                                               "scope": scope, "state": "xyz", "aud": FHIR, "launch": q["launch"][0],
                                               "code_challenge": challenge, "code_challenge_method": "S256"}, follow_redirects=False)
    assert r.status_code == 302, r.text
    cb = parse_qs(urlparse(r.headers["location"]).query)
    assert cb["state"] == ["xyz"]
    return client.post("/smart/token", data={"grant_type": "authorization_code", "code": cb["code"][0], "redirect_uri": APP_REDIRECT,
                                             "client_id": "cityforward-ai-dashboard", "code_verifier": verifier_override or verifier})


@pytest.fixture(scope="session")
def backend_key(client):
    """A backend service's key pair; the public half is registered with MediTrack by 'IT'."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = __import__("json").loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="test-key-1", alg="RS256", use="sig")
    r = client.post("/int/admin/smart/clients", json={"client_id": "test-backend", "name": "Test backend", "type": "BACKEND",
                                                      "jwks": {"keys": [jwk]}, "scopes": "system/*.read system/DocumentReference.write"})
    assert r.status_code == 200
    return key


def backend_assertion(key, aud=TOKEN_URL, ttl=120, client_id="test-backend"):
    t = int(time.time())
    return jwt.encode({"iss": client_id, "sub": client_id, "aud": aud, "iat": t, "exp": t + ttl, "jti": uuid.uuid4().hex}, key, algorithm="RS256", headers={"kid": "test-key-1"})


def backend_token(client, key, scope="system/*.read", **kw):
    return client.post("/smart/token", data={"grant_type": "client_credentials", "scope": scope,
                                             "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                                             "client_assertion": backend_assertion(key, **kw)})


def bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}
