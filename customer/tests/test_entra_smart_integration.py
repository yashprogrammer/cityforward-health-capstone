"""Run separately against a disposable DB with APP_ENV=deployment, AUTH_MODE=oidc."""
import base64
import hashlib
import os
import secrets
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from meditrack.backend import entra_login, staff_sessions
from meditrack.backend.app import app
from meditrack.backend.db import connect


@pytest.fixture
def secured_client(seeded, monkeypatch):
    assert os.environ['AUTH_MODE'] == 'oidc'
    monkeypatch.setenv('INTEGRATED_SMART_LAUNCH', '1')
    tenant, audience = os.environ['ENTRA_TENANT_ID'], os.environ['ENTRA_CLIENT_ID']
    issuer = f'https://login.microsoftonline.com/{tenant}/v2.0'
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = {'iss': issuer, 'aud': audience, 'exp': int(time.time()) + 300,
              'iat': int(time.time()), 'tid': tenant, 'oid': 'test-entra-user', 'nonce': 'test-nonce'}
    signed = jwt.encode(claims, key, algorithm='RS256')
    class Entra:
        def initiate_auth_code_flow(self, scopes, redirect_uri, state, response_mode):
            return {'auth_uri': 'https://login.example/signin', 'state': state, 'code_verifier': 'pkce'}
        def acquire_token_by_auth_code_flow(self, flow, response):
            return {'id_token': signed, 'id_token_claims': claims}
    monkeypatch.setattr(entra_login, '_client', lambda: Entra())
    monkeypatch.setattr(entra_login, '_keys', lambda tenant: SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())))
    staff_sessions.migrate()
    with connect() as c:
        c.execute('INSERT INTO staff_identity (issuer, subject, user_id) VALUES (%s,%s,%s) ON CONFLICT (issuer,subject,user_id) DO UPDATE SET active=true',
                  (issuer, 'test-entra-user', 'attending.raman'))
    with TestClient(app, base_url='https://ehr.example', follow_redirects=False) as client:
        yield client


def test_entra_session_binds_smart_access_and_logout(secured_client):
    client = secured_client
    assert client.get('/int/census').status_code == 401
    assert client.get('/auth/entra/start').status_code == 302
    with connect() as c:
        flow = c.execute('SELECT payload FROM staff_oidc_flow').fetchone()['PAYLOAD']
    assert client.get('/auth/entra/callback', params={'state': flow['state'], 'code': 'valid'}).status_code == 303
    assert client.get('/int/census').status_code == 200
    chart = client.get('/int/ip/IP2610-0142').json()
    smart = next(link for link in chart['links'] if link['LNK_ID'] == -1)
    launch = client.get(smart['HREF'])
    assert launch.status_code == 302
    context = parse_qs(urlparse(launch.headers['location']).query)['launch'][0]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    redirect = 'https://ehr.example/ai/auth/callback'
    params = {'response_type': 'code', 'client_id': 'cityforward-ai-dashboard', 'redirect_uri': redirect,
              'scope': 'launch openid patient/*.read', 'state': 'test', 'aud': 'https://ehr.example/fhir',
              'launch': context, 'code_challenge': challenge, 'code_challenge_method': 'S256'}
    authorize = client.get('/smart/authorize', params=params)
    assert authorize.status_code == 302
    code = parse_qs(urlparse(authorize.headers['location']).query)['code'][0]
    grant = client.post('/smart/token', data={'grant_type': 'authorization_code', 'code': code,
                        'redirect_uri': redirect, 'client_id': 'cityforward-ai-dashboard', 'code_verifier': verifier})
    assert grant.status_code == 200
    token = grant.json()['access_token']
    patient = grant.json()['patient']
    assert client.get('/fhir/Patient/' + patient, headers={'Authorization': 'Bearer ' + token}).status_code == 200
    assert client.post('/auth/logout', headers={'X-CSRF-Token': client.cookies['mt_csrf']}).status_code == 200
    assert client.get('/fhir/Patient/' + patient, headers={'Authorization': 'Bearer ' + token}).status_code == 401


def test_an_unassigned_microsoft_sign_in_is_refused_and_recorded_for_an_administrator(secured_client):
    client = secured_client
    with connect() as c:
        c.execute("UPDATE staff_identity SET active=false WHERE subject='test-entra-user'")
        c.execute("DELETE FROM staff_access_request WHERE subject='test-entra-user'")
        c.execute('DELETE FROM staff_oidc_flow')
    assert client.get('/auth/entra/start').status_code == 302
    with connect() as c:
        flow = c.execute('SELECT payload FROM staff_oidc_flow').fetchone()['PAYLOAD']
    denied = client.get('/auth/entra/callback', params={'state': flow['state'], 'code': 'valid'})
    assert denied.status_code == 403 and 'not assigned to a MediTrack staff account' in denied.text
    assert 'mt_session' not in client.cookies
    with connect() as c:
        request = c.execute("SELECT status, attempts FROM staff_access_request WHERE subject='test-entra-user'").fetchone()
    assert (request['STATUS'], request['ATTEMPTS']) == ('PENDING', 1)
