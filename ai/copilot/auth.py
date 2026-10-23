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

    def can_see_unit(self, unit: str | None) -> bool:
        return unit is None or "ALL" in self.units or unit in self.units


class Verifier:
    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=10.0)
        self._jwks: tuple[jwt.PyJWKSet, float] | None = None

    def _keys(self, refresh: bool = False) -> jwt.PyJWKSet:
        if refresh or not self._jwks or self._jwks[1] < time.time():
            r = self.http.get(f"{settings().meditrack_base}/smart/jwks.json")
            r.raise_for_status()
            self._jwks = (jwt.PyJWKSet.from_dict(r.json()), time.time() + 600)
        return self._jwks[0]

    def verify(self, token: str) -> Principal:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            for refresh in (False, True):        # a rotated key shows up as an unknown kid: refetch once
                keys = self._keys(refresh).keys
                key = next((k for k in keys if k.key_id == kid), None)
                if key:
                    break
            if key is None:
                raise HTTPException(401, "unknown signing key")
            claims = jwt.decode(token, key.key, algorithms=["RS256"], options={"verify_aud": False, "require": ["exp", "sub"]})
        except jwt.PyJWTError as e:
            raise HTTPException(401, f"invalid token: {e}") from e
        return principal_from_claims(claims)


def principal_from_claims(c: dict) -> Principal:
    scopes = (c.get("scope") or "").split()
    unit = c.get("unit")
    fhir_user = c.get("fhirUser")
    return Principal(user=str(c["sub"]), practitioner=fhir_user.rsplit("/", 1)[-1] if fhir_user else None, role=c.get("role") or "SERVICE", name=c.get("name", ""),
                     units=[unit] if unit else [], patients=[str(c["patient"])] if c.get("patient") else [], encounter=c.get("encounter"),
                     scopes=scopes, client_id=c.get("client_id", ""))


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
