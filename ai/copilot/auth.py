"""Who is calling? Verify the SMART access token MediTrack issued (RS256, via its JWKS) and turn its claims into a Principal.

The token is the only identity the service trusts; patient and unit scope come from it, never from request parameters.
"""
import time

import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from .config import settings


class Principal(BaseModel):
    user: str
    role: str = "SERVICE"          # ATTENDING | RESIDENT | NURSE | PA_COORD | HIM_CODER | IT_ADMIN (MediTrack roles) | SERVICE
    name: str = ""
    units: list[str] = []
    patients: list[str] = []       # patient ids this token is limited to (SMART launch context)
    practitioner: str | None = None
    encounter: str | None = None
    scopes: list[str] = []
    client_id: str = ""

    @property
    def is_system(self) -> bool:
        return self.role == "SYSTEM"

    def can_see_unit(self, unit: str | None) -> bool:
        return unit is None or "ALL" in self.units or unit in self.units


SYSTEM = Principal(user="system", role="SYSTEM", name="platform", units=["ALL"])


class Issuer(BaseModel):
    """A token issuer we trust. MediTrack issues the SMART launch tokens; the hospital's Keycloak issues staff SSO tokens."""
    name: str
    iss: str | None = None            # expected `iss` claim (None: not checked, MediTrack's tokens carry its FHIR base)
    jwks_url: str
    kind: str = "smart"               # smart | keycloak
    audience: str | None = None


def trusted_issuers() -> list[Issuer]:
    out = [Issuer(name="meditrack", jwks_url=f"{settings().meditrack_base}/smart/jwks.json", kind="smart")]
    if settings().keycloak_issuer:
        out.append(Issuer(name="keycloak", iss=settings().keycloak_issuer, jwks_url=f"{settings().keycloak_internal or settings().keycloak_issuer}/protocol/openid-connect/certs",
                          kind="keycloak", audience=settings().keycloak_audience))
    return out


class Verifier:
    def __init__(self, http: httpx.Client | None = None, issuers: list[Issuer] | None = None):
        self.http = http or httpx.Client(timeout=10.0)
        self.issuers = issuers
        self._jwks: dict[str, tuple[jwt.PyJWKSet, float]] = {}

    def _keys(self, iss: Issuer, refresh: bool = False) -> jwt.PyJWKSet:
        cached = self._jwks.get(iss.name)
        if refresh or not cached or cached[1] < time.time():
            r = self.http.get(iss.jwks_url)
            r.raise_for_status()
            cached = (jwt.PyJWKSet.from_dict(r.json()), time.time() + 600)
            self._jwks[iss.name] = cached
        return cached[0]

    def verify(self, token: str) -> Principal:
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as e:
            raise HTTPException(401, f"invalid token: {e}") from e
        issuers = self.issuers or trusted_issuers()
        candidates = [i for i in issuers if i.iss is None or i.iss == unverified.get("iss")]
        last = "no trusted issuer for this token"
        for iss in candidates:
            try:
                key = None
                for refresh in (False, True):    # a rotated key shows up as an unknown kid: refetch once
                    key = next((k for k in self._keys(iss, refresh).keys if k.key_id == kid), None)
                    if key:
                        break
                if key is None:
                    last = "unknown signing key"
                    continue
                opts = {"require": ["exp", "sub"], "verify_aud": bool(iss.audience)}
                claims = jwt.decode(token, key.key, algorithms=["RS256"], options=opts, **({"audience": iss.audience} if iss.audience else {}))
                return principal_from_keycloak(claims) if iss.kind == "keycloak" else principal_from_claims(claims)
            except jwt.PyJWTError as e:
                last = f"invalid token: {e}"
        raise HTTPException(401, last)


def principal_from_claims(c: dict) -> Principal:
    scopes = (c.get("scope") or "").split()
    unit = c.get("unit")
    fhir_user = c.get("fhirUser")
    return Principal(user=str(c["sub"]), practitioner=fhir_user.rsplit("/", 1)[-1] if fhir_user else None, role=c.get("role") or "SERVICE", name=c.get("name", ""),
                     units=[unit] if unit else [], patients=[str(c["patient"])] if c.get("patient") else [], encounter=c.get("encounter"),
                     scopes=scopes, client_id=c.get("client_id", ""))


KEYCLOAK_ROLES = ("ATTENDING", "RESIDENT", "NURSE", "PA_COORD", "HIM_CODER", "IT_ADMIN", "COMPLIANCE")


def principal_from_keycloak(c: dict) -> Principal:
    """Keycloak puts roles in realm_access.roles and our custom attributes (unit, practitioner) in top-level claims via protocol mappers."""
    roles = [r for r in (c.get("realm_access") or {}).get("roles", []) if r in KEYCLOAK_ROLES]
    if not roles:
        raise HTTPException(403, "token carries none of the hospital roles")
    units = c.get("units") or ([c["unit"]] if c.get("unit") else [])
    return Principal(user=str(c.get("preferred_username") or c["sub"]), role=roles[0], name=c.get("name", ""), units=list(units), practitioner=c.get("practitioner"),
                     scopes=(c.get("scope") or "").split(), client_id=c.get("azp", ""), patients=[])


_verifier = Verifier()


def set_verifier(v: Verifier) -> None:
    global _verifier
    _verifier = v


def current_principal(request: Request) -> Principal:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        raise HTTPException(401, "bearer token required", headers={"WWW-Authenticate": "Bearer"})
    return _verifier.verify(h[7:])


CurrentUser = Depends(current_principal)
