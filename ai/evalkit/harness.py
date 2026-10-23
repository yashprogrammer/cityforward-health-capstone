"""Bring up the whole hospital in one process: MediTrack (seeded), the mock payer and our service wiring. Used by the tests and by the eval runner.

Everything runs against the real code paths (FHIR over HTTP-in-process, SMART backend auth, Postgres, pgvector); only the network is replaced.
"""
import json
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path

AI = Path(__file__).resolve().parents[1]
CUSTOMER = AI.parent / "customer"


def configure_env(workdir: Path | None = None) -> None:
    """Defaults for an in-process run. Real environment variables win."""
    import sys
    for p in (str(AI), str(CUSTOMER)):
        if p not in sys.path:
            sys.path.insert(0, p)
    data = (workdir or AI / "tests" / ".data")
    os.environ.setdefault("WEBHOOK_DISPATCHER", "off")
    os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
    os.environ.setdefault("MEDITRACK_DB_URL", "postgresql://meditrack:meditrack@localhost:5432/meditrack")
    os.environ.setdefault("MEDITRACK_DOCS_DIR", str(data / "documents"))
    os.environ.setdefault("MEDITRACK_HOTFOLDER", str(data / "hotfolder"))
    os.environ.setdefault("COPILOT_DB_URL", "postgresql://copilot:copilot@localhost:5432/copilot")
    os.environ["MEDITRACK_BASE_URL"] = "http://testserver"
    os.environ.setdefault("EVENT_WORKER", "off")


@contextmanager
def meditrack_stack(seed: bool = True):
    """Yields the in-process MediTrack TestClient, with our backend client registered (`.pem` holds the private key)."""
    configure_env()
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient
    from meditrack.backend.app import app
    from meditrack.backend.seed import seed as seed_db
    if seed:
        seed_db()
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


@contextmanager
def full_stack(seed: bool = True):
    """MediTrack + payer + copilot wiring. Yields (meditrack_client, runtime)."""
    with ExitStack() as st:
        meditrack = st.enter_context(meditrack_stack(seed))
        from fastapi.testclient import TestClient
        from mock_payer import app as payer_app
        from copilot import auth, db, priorauth_api
        from copilot.app import rt
        from copilot.fhir_client import FhirClient
        from copilot.payer_client import PayerClient
        payer_app.REVIEW_SECONDS = 0.3
        payer = st.enter_context(TestClient(payer_app.app))
        db.init_schema()
        rt.fhir = FhirClient(http=meditrack, base="http://testserver", key_pem=meditrack.pem)
        auth.set_verifier(auth.Verifier(http=meditrack))
        priorauth_api.deps.payer = PayerClient(http=payer, base="http://payer", api_key=payer_app.API_KEY)
        try:
            yield meditrack, rt
        finally:
            db.close_pool()
