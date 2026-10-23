"""All configuration comes from environment variables (12-factor), read once here."""
import os
from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    db_url: str
    meditrack_base: str            # where MediTrack lives (FHIR at {base}/fhir, SMART at {base}/smart)
    client_id: str                 # our SMART backend-services client id, registered by hospital IT
    key_pem: str | None            # the private key for private_key_jwt (file or env); the public half is in MediTrack's SMT_CLIENT
    key_kid: str
    embedder: str                  # hash | fastembed
    embed_dim: int
    webhook_secret: str
    payer_base: str
    payer_api_key: str
    public_path: str               # URL prefix the gateway mounts us under


@lru_cache
def settings() -> Settings:
    pem = os.getenv("AI_BACKEND_KEY_PEM")
    if not pem and os.getenv("AI_BACKEND_KEY_FILE") and os.path.exists(os.environ["AI_BACKEND_KEY_FILE"]):
        pem = open(os.environ["AI_BACKEND_KEY_FILE"]).read()
    return Settings(
        db_url=os.getenv("COPILOT_DB_URL", "postgresql://copilot:copilot@localhost:5432/copilot"),
        meditrack_base=os.getenv("MEDITRACK_BASE_URL", "http://localhost:8001").rstrip("/"),
        client_id=os.getenv("AI_CLIENT_ID", "cityforward-ai-backend"),
        key_pem=pem, key_kid=os.getenv("AI_BACKEND_KEY_KID", "ai-backend-1"),
        embedder=os.getenv("EMBEDDER", "hash"), embed_dim=384,
        webhook_secret=os.getenv("AI_WEBHOOK_SECRET", "dev-webhook-secret"),
        payer_base=os.getenv("PAYER_BASE_URL", "http://localhost:8003").rstrip("/"),
        payer_api_key=os.getenv("PAYER_API_KEY", "demo-payer-key"),
        public_path=os.getenv("AI_PUBLIC_PATH", "/ai"))
