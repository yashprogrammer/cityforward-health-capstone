"""Single-tenant Entra sign-in, with a server-side one-time PKCE transaction."""
import hashlib
import json
import os
import secrets
from functools import lru_cache

import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import staff_admin, staff_sessions
from .db import connect, row

router = APIRouter(prefix='/auth/entra')


def _configuration():
    tenant = os.environ['ENTRA_TENANT_ID']
    client = os.environ['ENTRA_CLIENT_ID']
    base = os.environ['PUBLIC_BASE_URL'].rstrip('/')
    return tenant, client, base


def _client():
    """Authenticate the confidential client using the VM's managed identity."""
    import msal
    from azure.identity import ManagedIdentityCredential
    tenant, client, _ = _configuration()
    assertion = ManagedIdentityCredential(client_id=os.environ['ENTRA_MANAGED_IDENTITY_CLIENT_ID']).get_token('api://AzureADTokenExchange/.default').token
    return msal.ConfidentialClientApplication(client, authority=f'https://login.microsoftonline.com/{tenant}',
                                               client_credential={'client_assertion': assertion})


@lru_cache(maxsize=4)
def _keys(tenant):
    return jwt.PyJWKClient(f'https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys', cache_keys=True)


def _verified_identity(token, msal_claims):
    tenant, client, _ = _configuration()
    signing_key = _keys(tenant).get_signing_key_from_jwt(token).key
    claims = jwt.decode(token, signing_key, algorithms=['RS256'], audience=client,
                        issuer=f'https://login.microsoftonline.com/{tenant}/v2.0',
                        options={'require': ['iss', 'aud', 'exp', 'iat', 'tid', 'oid', 'nonce']})
    if (claims.get('tid') != tenant or claims.get('aud') != client
            or not secrets.compare_digest(str(claims.get('nonce', '')), str(msal_claims.get('nonce', '')))):
        raise ValueError('Entra token identity does not match the completed transaction')
    return claims['iss'], claims['oid'], claims


NO_ACCOUNT_PAGE = '''<!doctype html><meta charset="utf-8"><title>MediTrack HMS - access</title>
<body style="font-family:sans-serif;display:grid;place-items:center;min-height:100vh;background:#eef2f7;margin:0">
<main style="background:#fff;padding:32px;border-radius:8px;max-width:420px;box-shadow:0 4px 20px #0002">
<h1 style="margin-top:0">MediTrack HMS</h1>
<p><b>Your Microsoft account is not assigned to a MediTrack staff account.</b></p>
<p>Your sign-in was recorded as an access request. Ask IT (ext. 2204) to assign it, then sign in again.</p>
<p><a href="/">Back to sign in</a></p></main></body>'''


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@router.get('/start')
def start():
    _, _, base = _configuration()
    state = secrets.token_urlsafe(32)
    try:
        flow = _client().initiate_auth_code_flow(scopes=[], redirect_uri=base + '/auth/entra/callback',
                                                 state=state, response_mode='query')
        if not flow.get('auth_uri') or flow.get('state') != state:
            raise ValueError('invalid authorization response')
        ticket = secrets.token_urlsafe(32)
        with connect() as c:
            c.execute('DELETE FROM staff_oidc_flow WHERE expires_at < now()')
            c.execute("INSERT INTO staff_oidc_flow (flow_hash, payload, expires_at) VALUES (%s,%s::jsonb,now()+interval '10 minutes')",
                      (_digest(ticket), json.dumps(flow)))
    except Exception as exc:
        raise HTTPException(503, 'Entra sign-in is temporarily unavailable') from exc
    response = RedirectResponse(flow['auth_uri'], status_code=302, headers={'Cache-Control': 'no-store'})
    response.set_cookie('mt_oidc', ticket, httponly=True, secure=True, samesite='lax', max_age=600, path='/auth/entra/callback')
    return response


@router.get('/callback')
def callback(request: Request):
    ticket = request.cookies.get('mt_oidc', '')
    if not ticket or len(ticket) > 128 or any(len(request.query_params.getlist(k)) != 1 for k in request.query_params):
        raise HTTPException(400, 'Invalid sign-in transaction')
    with connect() as c:
        stored = row(c, 'DELETE FROM staff_oidc_flow WHERE flow_hash=%s AND expires_at > now() RETURNING payload', _digest(ticket))
    if not stored:
        raise HTTPException(400, 'Sign-in transaction is missing or expired')
    flow = stored['PAYLOAD']
    if not secrets.compare_digest(str(flow.get('state', '')), request.query_params.get('state', '')):
        raise HTTPException(400, 'Invalid sign-in transaction')
    try:
        result = _client().acquire_token_by_auth_code_flow(flow, dict(request.query_params))
        if not result.get('id_token') or not result.get('id_token_claims'):
            raise ValueError('missing identity token')
        issuer, subject, claims = _verified_identity(result['id_token'], result['id_token_claims'])
        session = staff_sessions.create_session(issuer, subject, request.cookies.get('mt_session'))
    except Exception as exc:
        raise HTTPException(401, 'Entra sign-in could not be verified') from exc
    if not session:
        try:      # keep the attempt so an administrator can assign this person from the staff dashboard
            staff_admin.record_access_request(issuer, subject, claims.get('name'), claims.get('email') or claims.get('preferred_username'))
        except Exception:
            pass
        return HTMLResponse(NO_ACCOUNT_PAGE, status_code=403, headers={'Cache-Control': 'no-store'})
    token, csrf = session
    response = RedirectResponse('/', status_code=303, headers={'Cache-Control': 'no-store'})
    response.delete_cookie('mt_oidc', path='/auth/entra/callback', secure=True, httponly=True, samesite='lax')
    response.set_cookie('mt_session', token, httponly=True, secure=True, samesite='lax', max_age=28800, path='/')
    response.set_cookie('mt_csrf', csrf, httponly=False, secure=True, samesite='lax', max_age=28800, path='/')
    return response
