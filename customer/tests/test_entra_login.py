"""OIDC transactions must be one-time and establish only provisioned identities."""
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meditrack.backend import entra_login

TENANT = '11111111-1111-1111-1111-111111111111'
CLIENT = '22222222-2222-2222-2222-222222222222'
ISSUER = f'https://login.microsoftonline.com/{TENANT}/v2.0'


class Store:
    def __init__(self):
        self.flows = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=None):
        import json
        if sql.startswith('INSERT INTO staff_oidc_flow'):
            self.flows[params[0]] = json.loads(params[1])
        elif sql.startswith('DELETE FROM staff_oidc_flow WHERE flow_hash'):
            self.result = self.flows.pop(params[0], None)
        return self

    def fetchone(self):
        return {'PAYLOAD': self.result} if self.result else None


@pytest.fixture
def oidc(monkeypatch):
    monkeypatch.setenv('ENTRA_TENANT_ID', TENANT)
    monkeypatch.setenv('ENTRA_CLIENT_ID', CLIENT)
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://ehr.example')
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(entra_login, '_keys', lambda tenant: SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())))
    claims = {'iss': ISSUER, 'aud': CLIENT, 'exp': int(time.time()) + 300,
              'iat': int(time.time()), 'tid': TENANT, 'oid': 'staff-object-id', 'nonce': 'nonce'}
    token = jwt.encode(claims, key, algorithm='RS256')
    class Entra:
        def initiate_auth_code_flow(self, scopes, redirect_uri, state, response_mode):
            assert scopes == []  # MSAL adds its reserved OpenID scopes itself.
            return {'auth_uri': 'https://login.example/signin', 'state': state, 'nonce': 'server-state', 'code_verifier': 'pkce'}
        def acquire_token_by_auth_code_flow(self, flow, response):
            if response.get('code') != 'valid':
                return {'error': 'invalid_grant'}
            return {'id_token': token, 'id_token_claims': claims}
    store = Store()
    monkeypatch.setattr(entra_login, '_client', lambda: Entra())
    monkeypatch.setattr(entra_login, 'connect', lambda: store)
    monkeypatch.setattr(entra_login.staff_sessions, 'create_session', lambda issuer, subject, previous: ('session-token', 'csrf-token') if (issuer, subject) == (ISSUER, 'staff-object-id') else None)
    app = FastAPI()
    app.include_router(entra_login.router)
    with TestClient(app, base_url='https://ehr.example', follow_redirects=False) as client:
        yield client, store, claims, key


def test_valid_login_consumes_transaction_and_sets_secure_cookies(oidc):
    client, store, _, _ = oidc
    assert client.get('/auth/entra/start').status_code == 302
    state = next(iter(store.flows.values()))['state']
    result = client.get('/auth/entra/callback', params={'code': 'valid', 'state': state})
    assert result.status_code == 303
    cookies = result.headers.get_list('set-cookie')
    assert any('mt_session=' in cookie and 'HttpOnly' in cookie and 'Secure' in cookie for cookie in cookies)
    assert client.get('/auth/entra/callback', params={'code': 'valid', 'state': state}).status_code == 400


def test_wrong_state_and_missing_identity_are_rejected(oidc, monkeypatch):
    client, store, _, _ = oidc
    client.get('/auth/entra/start')
    assert client.get('/auth/entra/callback', params={'code': 'valid', 'state': 'wrong'}).status_code == 400
    client.get('/auth/entra/start')
    state = next(iter(store.flows.values()))['state']
    monkeypatch.setattr(entra_login.staff_sessions, 'create_session', lambda *args: None)
    assert client.get('/auth/entra/callback', params={'code': 'valid', 'state': state}).status_code == 403


def test_missing_or_expired_transaction_is_rejected(oidc):
    client, store, _, _ = oidc
    assert client.get('/auth/entra/callback', params={'code': 'valid', 'state': 'missing'}).status_code == 400
    client.get('/auth/entra/start')
    state = next(iter(store.flows.values()))['state']
    store.flows.clear()  # The database removes expired transactions before returning them.
    assert client.get('/auth/entra/callback', params={'code': 'valid', 'state': state}).status_code == 400


def test_untrusted_or_expired_id_token_is_rejected(oidc):
    _, _, claims, key = oidc
    for changes in ({'aud': 'wrong'}, {'iss': 'https://wrong.example'}, {'exp': int(time.time()) - 1}, {'nonce': 'wrong'}):
        invalid = {**claims, **changes}
        with pytest.raises((jwt.PyJWTError, ValueError)):
            entra_login._verified_identity(jwt.encode(invalid, key, algorithm='RS256'), claims)
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(jwt.PyJWTError):
        entra_login._verified_identity(jwt.encode(claims, attacker_key, algorithm='RS256'), claims)
