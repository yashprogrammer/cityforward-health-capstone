"""Keep the local teaching login separate from any future cloud deployment."""
import os
import uuid
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AuthConfig:
    environment: str
    mode: str


def validate_auth_config(env: Mapping[str, str] | None = None) -> AuthConfig:
    env = os.environ if env is None else env
    environment = env.get("APP_ENV", "deployment").strip().lower()
    mode = env.get("AUTH_MODE", "oidc").strip().lower()
    if environment not in {"local", "deployment"}:
        raise RuntimeError("APP_ENV must be local or deployment")
    if mode not in {"demo", "local", "oidc"}:
        raise RuntimeError("AUTH_MODE must be demo, local or oidc")
    if mode == "local" and environment != "local":
        raise RuntimeError("Local password login is only permitted in the explicit local profile")
    if mode == "local" and env.get("FHIR_AUTH", "on").strip().lower() != "on":
        raise RuntimeError("FHIR authentication must be enabled with local staff login")
    if mode == "demo" and environment != "local":
        raise RuntimeError("Demo identity is only permitted in the explicit local profile")
    if environment == "deployment" and env.get("FHIR_AUTH", "on").strip().lower() == "off":
        raise RuntimeError("FHIR authentication must be enabled in deployed environments")
    if mode == "oidc":
        if environment != 'deployment' or env.get('ENABLE_ENTRA_OIDC') != '1':
            raise RuntimeError('Entra staff authentication is not enabled for deployment')
        try:
            for name in ('ENTRA_TENANT_ID', 'ENTRA_CLIENT_ID', 'ENTRA_MANAGED_IDENTITY_CLIENT_ID'):
                uuid.UUID(env[name])
            origin = urlsplit(env['PUBLIC_BASE_URL'])
            if origin.scheme != 'https' or not origin.hostname or origin.username or origin.password or origin.path not in ('', '/') or origin.query or origin.fragment:
                raise ValueError('invalid public origin')
        except (KeyError, ValueError, TypeError) as exc:
            raise RuntimeError('Entra staff authentication requires valid IDs and a public HTTPS origin') from exc
    return AuthConfig(environment=environment, mode=mode)
