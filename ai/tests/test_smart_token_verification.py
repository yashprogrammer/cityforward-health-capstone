import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from copilot.auth import Issuer, Verifier


def test_smart_token_rejects_wrong_issuer_audience_and_id_token():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update(kid='smart-1', alg='RS256', use='sig')
    http = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'keys': [public]})))
    issuer = 'https://ehr.example'
    audience = issuer + '/fhir'
    verifier = Verifier(http=http, issuers=[Issuer(name='meditrack', iss=issuer, audience=audience, jwks_url=issuer + '/jwks')])
    def mint(**kw):
        claims = {'iss': issuer, 'aud': audience, 'sub': 'attending', 'role': 'ATTENDING', 'unit': '4W-CARD',
                  'patient': 'p1', 'exp': int(time.time()) + 300}
        claims.update(kw)
        return jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'smart-1'})
    assert verifier.verify(mint()).patients == ['p1']
    for changes in ({'iss': 'https://other.example'}, {'aud': 'other'}, {'aud': 'dashboard-client'}):
        with pytest.raises(HTTPException) as e:
            verifier.verify(mint(**changes))
        assert e.value.status_code == 401


def test_session_bound_token_rechecked_and_fails_closed(monkeypatch):
    from types import SimpleNamespace
    from copilot import auth
    from copilot.app import rt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='smart-1', alg='RS256', use='sig')
    issuer, audience, reference = 'https://ehr.example', 'https://ehr.example/fhir', 'a' * 64
    active = {'value': True}
    def response(req):
        if req.url.path == '/jwks':
            return httpx.Response(200, json={'keys': [jwk]})
        assert req.headers['authorization'] == 'Bearer service-test-token'
        return httpx.Response(200, json={'active': active['value'], 'user': 'attending', 'role': 'ATTENDING', 'unit': '4W-CARD'})
    http = httpx.Client(transport=httpx.MockTransport(response))
    monkeypatch.setattr(auth, 'settings', lambda: SimpleNamespace(smart_session_required=True, meditrack_base=issuer))
    monkeypatch.setattr(rt, 'get_fhir', lambda: SimpleNamespace(token=lambda: 'service-test-token'))
    verifier = Verifier(http=http, issuers=[Issuer(name='meditrack', iss=issuer, audience=audience, jwks_url=issuer + '/jwks')])
    def mint(**changes):
        claims = {'iss': issuer, 'aud': audience, 'sub': 'attending', 'role': 'ATTENDING', 'unit': '4W-CARD',
                  'patient': 'p1', 'exp': int(time.time()) + 300, 'staff_session': reference, 'token_use': 'access'}
        claims.update(changes)
        return jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'smart-1'})
    assert verifier.verify(mint()).patients == ['p1']
    with pytest.raises(HTTPException) as wrong_role:
        verifier.verify(mint(role='NURSE'))
    assert wrong_role.value.status_code == 401
    active['value'] = False
    with pytest.raises(HTTPException) as revoked:
        verifier.verify(mint())
    assert revoked.value.status_code == 401
    with pytest.raises(HTTPException) as malformed:
        verifier.verify(mint(staff_session='../invalid'))
    assert malformed.value.status_code == 401
