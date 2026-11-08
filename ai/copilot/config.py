"""All configuration comes from environment variables (12-factor), read once here."""
import os
from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    db_url: str
    meditrack_base: str            # where MediTrack lives (FHIR at {base}/fhir, SMART at {base}/smart)
    smart_issuer: str              # public issuer in SMART tokens (may differ from in-cluster URL)
    smart_audience: str             # expected FHIR audience, never inferred from the presented token
    smart_session_required: bool   # secured deployments check the originating staff session on each request
    client_id: str                 # our SMART backend-services client id, registered by hospital IT
    key_pem: str | None            # the private key for private_key_jwt (file or env); the public half is in MediTrack's SMT_CLIENT
    key_kid: str
    embedder: str                  # hash | fastembed
    embed_dim: int
    webhook_secret: str
    payer_base: str
    payer_api_key: str
    public_path: str               # URL prefix the gateway mounts us under
    keycloak_issuer: str | None    # e.g. https://idp.hospital.example/realms/cityforward ; unset = only MediTrack SMART tokens are trusted
    keycloak_internal: str | None  # in-cluster URL of the same realm, for fetching the JWKS
    keycloak_audience: str | None


@lru_cache
def settings() -> Settings:
    pem = os.getenv("AI_BACKEND_KEY_PEM")
    if not pem and os.getenv("AI_BACKEND_KEY_FILE") and os.path.exists(os.environ["AI_BACKEND_KEY_FILE"]):
        pem = open(os.environ["AI_BACKEND_KEY_FILE"]).read()
    meditrack_base = os.getenv("MEDITRACK_BASE_URL", "http://localhost:8001").rstrip("/")
    smart_issuer = os.getenv("MEDITRACK_SMART_ISSUER", meditrack_base).rstrip("/")
    return Settings(
        db_url=os.getenv("COPILOT_DB_URL", "postgresql://copilot:copilot@localhost:5432/copilot"),
        meditrack_base=meditrack_base, smart_issuer=smart_issuer,
        smart_audience=os.getenv("MEDITRACK_SMART_AUDIENCE", smart_issuer + "/fhir"),
        smart_session_required=os.getenv("SMART_SESSION_REQUIRED", "true").lower() != "false",
        client_id=os.getenv("AI_CLIENT_ID", "cityforward-ai-backend"),
        key_pem=pem, key_kid=os.getenv("AI_BACKEND_KEY_KID", "ai-backend-1"),
        embedder=os.getenv("EMBEDDER", "hash"), embed_dim=384,
        webhook_secret=os.getenv("AI_WEBHOOK_SECRET", "dev-webhook-secret"),
        payer_base=os.getenv("PAYER_BASE_URL", "http://localhost:8003").rstrip("/"),
        payer_api_key=os.getenv("PAYER_API_KEY", "demo-payer-key"),
        public_path=os.getenv("AI_PUBLIC_PATH", "/ai"), keycloak_issuer=os.getenv("KEYCLOAK_ISSUER"), keycloak_internal=os.getenv("KEYCLOAK_INTERNAL_URL"),
        keycloak_audience=os.getenv("KEYCLOAK_AUDIENCE"))
