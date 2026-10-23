import json
import os
import sys
from pathlib import Path

import pytest

AI = Path(__file__).resolve().parents[1]
CUSTOMER = AI.parent / "customer"
sys.path[:0] = [str(AI), str(CUSTOMER)]
os.environ.setdefault("WEBHOOK_DISPATCHER", "off")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("MEDITRACK_DB_URL", "postgresql://meditrack:meditrack@localhost:5432/meditrack")
os.environ.setdefault("MEDITRACK_DOCS_DIR", str(AI / "tests" / ".data" / "documents"))
os.environ.setdefault("MEDITRACK_HOTFOLDER", str(AI / "tests" / ".data" / "hotfolder"))
os.environ.setdefault("COPILOT_DB_URL", "postgresql://copilot:copilot@localhost:5432/copilot")
os.environ["MEDITRACK_BASE_URL"] = "http://testserver"
os.environ["LLM_PROVIDER"] = "offline"

from copilot.llm import Reply   # noqa: E402


class ScriptedLLM:
    """A fake model backend that replays canned replies (str / dict / Exception) and records the prompts it was sent."""
    name = "scripted:test"

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def chat(self, system, user):
        self.prompts.append((system, user))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        text = r if isinstance(r, str) else json.dumps(r)
        return Reply(text, tokens_in=len(system + user) // 4, tokens_out=len(text) // 4)


@pytest.fixture(scope="session")
def meditrack():
    """The customer's EHR, seeded, running in-process; our backend client registered with it (what hospital IT does)."""
    import psycopg
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import jwt
    try:
        psycopg.connect(os.environ["MEDITRACK_DB_URL"], connect_timeout=3).close()
        psycopg.connect(os.environ["COPILOT_DB_URL"], connect_timeout=3).close()
    except psycopg.Error as e:   # pragma: no cover
        pytest.skip(f"PostgreSQL not reachable ({e})")
    from fastapi.testclient import TestClient
    from meditrack.backend.app import app
    from meditrack.backend.seed import seed
    seed()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="ai-backend-1", alg="RS256", use="sig")
    with TestClient(app) as c:
        r = c.post("/int/admin/smart/clients", json={"client_id": "cityforward-ai-backend", "name": "AI backend", "type": "BACKEND", "jwks": {"keys": [jwk]},
                                                     "scopes": "system/*.read system/DocumentReference.write"})
        assert r.status_code == 200, r.text
        c.pem = pem
        yield c


@pytest.fixture(scope="session")
def rt(meditrack):
    from copilot import auth, db
    from copilot.app import rt as runtime
    from copilot.fhir_client import FhirClient
    db.init_schema()
    runtime.fhir = FhirClient(http=meditrack, base="http://testserver", key_pem=meditrack.pem)
    auth.set_verifier(auth.Verifier(http=meditrack))
    yield runtime
    db.close_pool()


@pytest.fixture()
def clean_db(rt):
    from copilot import db
    with db.session() as c:
        c.execute("TRUNCATE chunk, patient_index, llm_call RESTART IDENTITY")
        for t in ("audit_log", "event_inbox", "prior_auth", "coding_suggestion", "eval_run"):
            if c.execute("SELECT to_regclass(%s) r", (t,)).fetchone()["r"]:
                c.execute(f"TRUNCATE {t} RESTART IDENTITY CASCADE")
    return True


@pytest.fixture(scope="session")
def client(rt):
    from fastapi.testclient import TestClient
    from copilot.app import app
    with TestClient(app) as c:
        yield c


# ---- user tokens: the same SMART EHR-launch MediTrack gives a real app
sys.path.insert(0, str(CUSTOMER / "tests"))


def user_token(meditrack, ip_no, user="attending.raman", scope="launch openid fhirUser patient/*.read user/Practitioner.read patient/DocumentReference.write"):
    import conftest as cust   # customer/tests/conftest.py helpers are importable because of the sys.path entry above
    link = next((l for l in meditrack.get("/int/admin/extlinks").json() if l["DISP_MD"] == "SMART"), None)
    if link is None:
        meditrack.post("/int/admin/extlinks", json={"label": "AI Clinical Insights", "url": "http://localhost:8001/ai/launch", "mode": "SMART"})
        link = next(l for l in meditrack.get("/int/admin/extlinks").json() if l["DISP_MD"] == "SMART")
    r = cust.ehr_launch_token(meditrack, link["LNK_ID"], ip_no, scope=scope, user=user)
    assert r.status_code == 200, r.text
    return r.json()


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}
