"""SMART on FHIR authorization server (MediTrack v3.4).

Two ways in, both from the SMART App Launch / Backend Services specs:

  1. EHR launch (clinician apps)   GET /smart/ehr-launch -> app -> GET /smart/authorize -> POST /smart/token
     authorization code + PKCE (S256); the token carries the patient and encounter the clinician was looking at.
  2. Backend services (servers)    POST /smart/token  grant_type=client_credentials with a signed JWT assertion
     (private_key_jwt); the token carries system/* scopes. No user, no browser.

The EHR is the authorization server (as in Epic / Oracle Health). MediTrack has no SSO, so the "user" of an EHR launch
is whoever is signed in to MediTrack (cookie `mt_user`, switchable in the banner for demos).
"""
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from base64 import urlsafe_b64encode
from pathlib import Path
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from .db import FILES_DIR, audit, connect, now, row, rows

router = APIRouter()

BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8001").rstrip("/")
FHIR_BASE = BASE_URL + "/fhir"
TOKEN_URL = BASE_URL + "/smart/token"
ACCESS_TTL = int(os.getenv("SMART_ACCESS_TTL", "3600"))
BACKEND_TTL = 300                       # SMART Backend Services: short-lived tokens
LAUNCH_TTL = 600
CODE_TTL = 60
DEFAULT_USER = "res.etran"
SCOPE_RE = re.compile(r"(patient|user|system)/(\*|[A-Za-z]+)\.(read|write|\*)")
STATIC_SCOPES = {"launch", "openid", "fhirUser", "profile", "online_access", "offline_access"}

_key = None
_seen_jti: dict[str, float] = {}


# ---------------------------------------------------------------- signing keys
def signing_key():
    """RS256 key: SMART_SIGNING_KEY_PEM (Kubernetes secret) or a key persisted next to the data, created on first use."""
    global _key
    if _key is None:
        pem = os.getenv("SMART_SIGNING_KEY_PEM")
        path = FILES_DIR / "smart_signing_key.pem"
        if pem:
            _key = serialization.load_pem_private_key(pem.encode(), password=None)
        elif path.exists():
            _key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        else:
            _key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return _key


def public_jwk() -> dict:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key().public_key()))
    jwk.update(use="sig", alg="RS256")
    jwk["kid"] = urlsafe_b64encode(hashlib.sha256(json.dumps(jwk, sort_keys=True).encode()).digest())[:16].decode()
    return jwk


def issue_token(claims: dict, ttl: int, audience: str = FHIR_BASE) -> str:
    t = int(time.time())
    payload = {"iss": BASE_URL, "aud": audience, "iat": t, "exp": t + ttl, "jti": uuid.uuid4().hex, **claims}
    return jwt.encode(payload, signing_key(), algorithm="RS256", headers={"kid": public_jwk()["kid"]})


def verify_access_token(token: str) -> dict:
    return jwt.decode(token, signing_key().public_key(), algorithms=["RS256"], audience=FHIR_BASE, issuer=BASE_URL)


def scope_allows(claims: dict, rtype: str, action: str = "read", patient_id=None) -> bool:
    """SMART v1 scope check: <patient|user|system>/<Resource|*>.<read|write|*>. patient/* scopes are limited to the token's patient."""
    for sc in claims.get("scope", "").split():
        m = SCOPE_RE.fullmatch(sc)
        if not m:
            continue
        ctx, res, act = m.groups()
        if res not in ("*", rtype) or act not in ("*", action):
            continue
        if ctx == "patient" and patient_id is not None and str(claims.get("patient")) != str(patient_id):
            continue
        return True
    return False


# ---------------------------------------------------------------- discovery
@router.get("/fhir/.well-known/smart-configuration")
@router.get("/smart/.well-known/smart-configuration", include_in_schema=False)
def smart_configuration():
    return {
        "issuer": BASE_URL, "jwks_uri": BASE_URL + "/smart/jwks.json",
        "authorization_endpoint": BASE_URL + "/smart/authorize", "token_endpoint": TOKEN_URL,
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "response_types_supported": ["code"],
        "token_endpoint_auth_methods_supported": ["private_key_jwt", "none"],
        "token_endpoint_auth_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["launch", "openid", "fhirUser", "patient/*.read", "user/*.read", "user/DocumentReference.write", "system/*.read", "system/DocumentReference.write"],
        "code_challenge_methods_supported": ["S256"],
        "capabilities": ["launch-ehr", "client-public", "client-confidential-asymmetric", "context-ehr-patient", "context-ehr-encounter",
                         "sso-openid-connect", "permission-patient", "permission-user", "permission-v1"],
    }


@router.get("/smart/jwks.json")
def jwks():
    return {"keys": [public_jwk()]}


# ---------------------------------------------------------------- EHR launch
def current_user(request: Request) -> dict:
    uid = request.cookies.get("mt_user", DEFAULT_USER)
    with connect() as c:
        u = row(c, "SELECT * FROM USR_MST WHERE USR_ID=%s AND IS_ACTV=1", uid) or row(c, "SELECT * FROM USR_MST WHERE USR_ID=%s", DEFAULT_USER)
    return u


@router.get("/smart/ehr-launch")
def ehr_launch(request: Request, lnk_id: int, ip_no: str):
    """Opened by MediTrack's patient file for a SMART link: creates the launch context and sends the browser to the app."""
    user = current_user(request)
    with connect() as c:
        lnk = row(c, "SELECT * FROM SYS_EXT_LNK WHERE LNK_ID=%s AND IS_ACTV=1 AND DISP_MD='SMART'", lnk_id)
        adm = row(c, "SELECT IP_NO, PT_ID FROM IP_ADM_DTL WHERE IP_NO=%s", ip_no)
        if not lnk or not adm:
            raise HTTPException(404, "UNKNOWN LINK OR ACCOUNT")
        launch = secrets.token_urlsafe(18)
        c.execute("INSERT INTO SMT_LAUNCH VALUES (%s,%s,%s,%s,%s)", (launch, user["USR_ID"], ip_no, adm["PT_ID"], str(time.time())))
        audit(c, user["USR_ID"], "SMART_LAUNCH", f"{lnk['LNK_LBL']}:{ip_no}")
    sep = "&" if "?" in lnk["LNK_URL"] else "?"
    return RedirectResponse(f"{lnk['LNK_URL']}{sep}{urlencode({'iss': FHIR_BASE, 'launch': launch})}", status_code=302)


def _fail_page(msg: str, status: int = 400) -> HTMLResponse:
    return HTMLResponse(f"<h3>SMART authorization error</h3><p>{msg}</p>", status_code=status)


def _redirect_error(redirect_uri: str, state: str | None, error: str, desc: str) -> RedirectResponse:
    q = {"error": error, "error_description": desc, **({"state": state} if state else {})}
    return RedirectResponse(f"{redirect_uri}{'&' if '?' in redirect_uri else '?'}{urlencode(q)}", status_code=302)


def check_scopes(requested: str, allowed: str, backend: bool = False) -> str | None:
    """Return the first requested scope the client may not have (None = all fine)."""
    allowed_set = set(allowed.split())
    for sc in requested.split():
        if sc in STATIC_SCOPES:
            continue
        if sc not in allowed_set and not _covered(sc, allowed_set):
            return sc
        if backend and not sc.startswith("system/"):
            return sc
    return None


def _covered(scope: str, allowed: set[str]) -> bool:
    m = SCOPE_RE.fullmatch(scope)
    return bool(m) and any((a := SCOPE_RE.fullmatch(x)) and a[1] == m[1] and a[2] in ("*", m[2]) and a[3] in ("*", m[3]) for x in allowed)


@router.get("/smart/authorize")
def authorize(request: Request, response_type: str = "", client_id: str = "", redirect_uri: str = "", scope: str = "", state: str | None = None,
              aud: str = "", launch: str | None = None, code_challenge: str = "", code_challenge_method: str = ""):
    with connect() as c:
        client = row(c, "SELECT * FROM SMT_CLIENT WHERE CLIENT_ID=%s AND IS_ACTV=1 AND CLIENT_TYP='PUBLIC'", client_id)
        if not client:
            return _fail_page("Unknown or inactive client_id.")
        if redirect_uri not in client["REDIRECT_URIS"].split():
            return _fail_page("redirect_uri is not registered for this client.")   # never redirect to an unregistered URI
        if response_type != "code":
            return _redirect_error(redirect_uri, state, "unsupported_response_type", "only response_type=code")
        if aud.rstrip("/") != FHIR_BASE:
            return _redirect_error(redirect_uri, state, "invalid_request", f"aud must be {FHIR_BASE}")
        if code_challenge_method != "S256" or not code_challenge:
            return _redirect_error(redirect_uri, state, "invalid_request", "PKCE with S256 is required")
        bad = check_scopes(scope, client["SCOPES"])
        if bad:
            return _redirect_error(redirect_uri, state, "invalid_scope", f"scope not allowed for this client: {bad}")
        ctx = row(c, "SELECT * FROM SMT_LAUNCH WHERE LAUNCH_ID=%s", launch) if launch else None
        if "launch" in scope.split() and (not ctx or time.time() - float(ctx["CRT_TS"]) > LAUNCH_TTL):
            return _redirect_error(redirect_uri, state, "invalid_request", "missing, unknown or expired launch context")
        if not ctx:
            return _redirect_error(redirect_uri, state, "invalid_request", "only EHR launch is supported by this server")
        code = secrets.token_urlsafe(32)
        c.execute("INSERT INTO SMT_CODE VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0)",
                  (code, client_id, redirect_uri, scope, ctx["USR_ID"], launch, code_challenge, time.time() + CODE_TTL))
        audit(c, ctx["USR_ID"], "SMART_AUTHORIZE", f"{client_id}:{ctx['IP_NO']}")
    # trusted, pre-registered clinician app: no per-launch consent screen (hospital policy approves the app, not each launch)
    q = {"code": code, **({"state": state} if state else {})}
    return RedirectResponse(f"{redirect_uri}{'&' if '?' in redirect_uri else '?'}{urlencode(q)}", status_code=302)


# ---------------------------------------------------------------- token endpoint
def _err(status: int, error: str, desc: str) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": desc}, status_code=status, headers={"Cache-Control": "no-store"})


@router.post("/smart/token")
def token(grant_type: str = Form(""), code: str = Form(""), redirect_uri: str = Form(""), code_verifier: str = Form(""), client_id: str = Form(""),
          client_assertion_type: str = Form(""), client_assertion: str = Form(""), scope: str = Form("")):
    if grant_type == "authorization_code":
        return _code_grant(code, redirect_uri, code_verifier, client_id)
    if grant_type == "client_credentials":
        return _backend_grant(client_assertion_type, client_assertion, scope)
    return _err(400, "unsupported_grant_type", "authorization_code or client_credentials")


def _code_grant(code: str, redirect_uri: str, verifier: str, client_id: str):
    with connect() as c:
        rec = row(c, "SELECT * FROM SMT_CODE WHERE CODE=%s", code)
        if not rec or rec["USED"] or rec["EXP_TS"] < time.time():
            return _err(400, "invalid_grant", "unknown, used or expired authorization code")
        c.execute("UPDATE SMT_CODE SET USED=1 WHERE CODE=%s", (code,))   # single use, even if the checks below fail
        if rec["CLIENT_ID"] != client_id or rec["REDIRECT_URI"] != redirect_uri:
            return _err(400, "invalid_grant", "client_id or redirect_uri does not match the authorization request")
        digest = urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not verifier or not secrets.compare_digest(digest, rec["CHALLENGE"]):
            return _err(400, "invalid_grant", "PKCE verification failed")
        launch = row(c, "SELECT * FROM SMT_LAUNCH WHERE LAUNCH_ID=%s", rec["LAUNCH_ID"])
        user = row(c, "SELECT * FROM USR_MST WHERE USR_ID=%s", rec["USR_ID"])
        doc = row(c, "SELECT DR_NM FROM DR_MST WHERE DR_ID=%s", user["DR_ID"]) if user["DR_ID"] else None
        audit(c, user["USR_ID"], "SMART_TOKEN", f"{client_id}:{launch['IP_NO']}")
    claims = {"sub": user["USR_ID"], "client_id": client_id, "scope": rec["SCOPE"], "patient": str(launch["PT_ID"]), "encounter": launch["IP_NO"],
              "name": user["USR_NM"], "role": user["ROLE_CD"], "unit": user["UNIT_CD"],
              **({"fhirUser": f"{FHIR_BASE}/Practitioner/{user['DR_ID']}"} if user["DR_ID"] else {})}
    body = {"access_token": issue_token(claims, ACCESS_TTL), "token_type": "Bearer", "expires_in": ACCESS_TTL, "scope": rec["SCOPE"],
            "patient": claims["patient"], "encounter": claims["encounter"], "need_patient_banner": True}
    if "openid" in rec["SCOPE"].split():
        body["id_token"] = issue_token({k: claims[k] for k in ("sub", "name", "role", "unit") if k in claims} | ({"fhirUser": claims["fhirUser"]} if "fhirUser" in claims else {}),
                                       ACCESS_TTL, audience=client_id)
    return JSONResponse(body, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _backend_grant(assertion_type: str, assertion: str, scope: str):
    if assertion_type != "urn:ietf:params:oauth:client-assertion-type:jwt-bearer" or not assertion:
        return _err(401, "invalid_client", "a signed client_assertion is required")
    try:
        unverified = jwt.decode(assertion, options={"verify_signature": False})
        cid = unverified.get("iss", "")
    except jwt.PyJWTError:
        return _err(401, "invalid_client", "client_assertion is not a JWT")
    with connect() as c:
        client = row(c, "SELECT * FROM SMT_CLIENT WHERE CLIENT_ID=%s AND IS_ACTV=1 AND CLIENT_TYP='BACKEND'", cid)
    if not client or not client["JWKS"]:
        return _err(401, "invalid_client", "unknown or inactive backend client")
    try:
        kid = jwt.get_unverified_header(assertion).get("kid")
        keys = jwt.PyJWKSet.from_dict(json.loads(client["JWKS"])).keys
        key = next((k for k in keys if k.key_id == kid), keys[0] if len(keys) == 1 else None)
        if key is None:
            return _err(401, "invalid_client", "no registered key matches the assertion kid")
        claims = jwt.decode(assertion, key.key, algorithms=["RS256"], audience=TOKEN_URL, options={"require": ["exp", "iss", "sub", "aud", "jti"]})
    except jwt.PyJWTError as e:
        return _err(401, "invalid_client", f"client_assertion rejected: {e}")
    if claims["sub"] != cid or claims["exp"] - time.time() > 300:
        return _err(401, "invalid_client", "sub must equal iss and the assertion may live at most 5 minutes")
    t = time.time()
    for j in [j for j, e in _seen_jti.items() if e < t]:
        _seen_jti.pop(j)
    if claims["jti"] in _seen_jti:
        return _err(401, "invalid_client", "assertion replay (jti already used)")
    _seen_jti[claims["jti"]] = claims["exp"]
    bad = check_scopes(scope, client["SCOPES"], backend=True)
    if not scope or bad:
        return _err(400, "invalid_scope", f"scope not allowed for this client: {bad or '(empty)'}")
    with connect() as c:
        audit(c, cid, "SMART_BACKEND_TOKEN", scope)
    return JSONResponse({"access_token": issue_token({"sub": cid, "client_id": cid, "scope": scope}, BACKEND_TTL), "token_type": "Bearer",
                         "expires_in": BACKEND_TTL, "scope": scope}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


# ---------------------------------------------------------------- admin: client registration (the IT admin's job)
class ClientIn(BaseModel):
    client_id: str
    name: str
    type: str                          # PUBLIC | BACKEND
    redirect_uris: str = ""
    jwks: dict | None = None
    scopes: str


@router.get("/int/admin/smart/clients")
def list_clients():
    with connect() as c:
        return rows(c, "SELECT CLIENT_ID, CLIENT_NM, CLIENT_TYP, REDIRECT_URIS, SCOPES, IS_ACTV, CRT_DT FROM SMT_CLIENT ORDER BY CLIENT_ID")


@router.post("/int/admin/smart/clients")
def register_client(body: ClientIn):
    if body.type not in ("PUBLIC", "BACKEND"):
        raise HTTPException(400, "TYPE MUST BE PUBLIC OR BACKEND")
    if body.type == "BACKEND" and not body.jwks:
        raise HTTPException(400, "BACKEND CLIENTS NEED A JWKS")
    with connect() as c:
        c.execute("""INSERT INTO SMT_CLIENT VALUES (%s,%s,%s,%s,%s,%s,1,%s) ON CONFLICT (CLIENT_ID) DO UPDATE SET CLIENT_NM=EXCLUDED.CLIENT_NM,
                     CLIENT_TYP=EXCLUDED.CLIENT_TYP, REDIRECT_URIS=EXCLUDED.REDIRECT_URIS, JWKS=EXCLUDED.JWKS, SCOPES=EXCLUDED.SCOPES, IS_ACTV=1""",
                  (body.client_id, body.name, body.type, body.redirect_uris, json.dumps(body.jwks) if body.jwks else None, body.scopes, now()))
        audit(c, "it.admin", "SMART_CLIENT_REGISTERED", body.client_id)
    return {"ok": True}


@router.post("/int/admin/smart/clients/{client_id}/toggle")
def toggle_client(client_id: str):
    with connect() as c:
        c.execute("UPDATE SMT_CLIENT SET IS_ACTV = 1 - IS_ACTV WHERE CLIENT_ID=%s", (client_id,))
    return {"ok": True}


@router.get("/int/users")
def list_users():
    """The staff list for the banner's 'act as' switch (demo only: MediTrack has no SSO)."""
    with connect() as c:
        return rows(c, "SELECT USR_ID, USR_NM, ROLE_CD, UNIT_CD FROM USR_MST WHERE IS_ACTV=1 ORDER BY USR_ID")
