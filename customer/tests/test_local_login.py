"""Run against a disposable, seeded database with AUTH_MODE=local."""
from urllib.parse import parse_qs, urlparse

from conftest import APP_REDIRECT, FHIR, _pkce, bearer
from meditrack.backend import local_login, smart, staff_sessions
from meditrack.backend.db import connect


def test_local_login_requires_password_and_revokes_session(client):
    local_login.set_password('attending.raman', 'local-test-password-123')
    local_login.set_password('nurse.lopez', 'local-test-password-456')
    client.cookies.clear()
    assert client.get('/int/census').status_code == 401
    assert client.post('/auth/login', headers={'Origin': 'http://other.example'}, json={'username': 'attending.raman', 'password': 'wrong'}).status_code == 403
    assert client.post('/auth/login', json={'username': 'attending.raman', 'password': 'wrong'}).status_code == 401
    assert client.post('/auth/login', json={'username': 'attending.raman', 'password': 'local-test-password-123'}).status_code == 200
    session = client.get('/auth/session').json()
    assert session['user'] == 'attending.raman' and session['role'] == 'ATTENDING'
    # A browser-controlled identity cookie cannot change the signed-in staff member.
    client.cookies.set('mt_user', 'it.admin')
    assert client.get('/auth/session').json()['user'] == 'attending.raman'
    assert client.get('/int/census').status_code == 200
    assert client.get('/int/admin/extlinks').status_code == 403
    csrf = client.cookies.get('mt_csrf')
    assert client.post('/int/ip/IP2610-0142/manual-summary', json={'text': 'test'}).status_code == 403
    assert client.post('/int/ip/IP2610-0142/manual-summary', headers={'X-CSRF-Token': csrf}, json={'text': 'test'}).status_code == 200
    token = client.cookies.get('mt_session')
    with connect() as c:
        link_id = c.execute("INSERT INTO SYS_EXT_LNK (LNK_LBL,LNK_URL,DISP_MD,IS_ACTV,CRT_BY,CRT_DT) VALUES ('Test app','http://testserver/ai/launch','SMART',1,'test','2026-01-01') RETURNING LNK_ID").fetchone()['LNK_ID']
    launch = client.get(f'/smart/ehr-launch?lnk_id={link_id}&ip_no=IP2610-0142', follow_redirects=False)
    assert launch.status_code == 302
    context = parse_qs(urlparse(launch.headers['location']).query)['launch'][0]
    verifier, challenge = _pkce()
    params = {'response_type': 'code', 'client_id': 'cityforward-ai-dashboard', 'redirect_uri': APP_REDIRECT,
              'scope': 'launch openid patient/*.read', 'state': 'test', 'aud': FHIR, 'launch': context,
              'code_challenge': challenge, 'code_challenge_method': 'S256'}
    auth = client.get('/smart/authorize', params=params, follow_redirects=False)
    code = parse_qs(urlparse(auth.headers['location']).query)['code'][0]
    grant = client.post('/smart/token', data={'grant_type': 'authorization_code', 'code': code,
                        'redirect_uri': APP_REDIRECT, 'client_id': 'cityforward-ai-dashboard', 'code_verifier': verifier})
    assert grant.status_code == 200
    access, patient = grant.json()['access_token'], grant.json()['patient']
    assert client.get('/fhir/Patient/' + patient, headers=bearer(access)).status_code == 200
    service = smart.issue_token({'sub': 'cityforward-ai-backend', 'client_id': 'cityforward-ai-backend',
                                 'scope': 'system/*.read', 'token_use': 'service'}, 60)
    reference = staff_sessions.session_reference(token)
    assert client.get('/smart/session-status/' + reference, headers=bearer(service)).json()['active'] is True
    assert client.post('/auth/logout', headers={'X-CSRF-Token': csrf}).status_code == 200
    assert staff_sessions.get_session(token) is None
    assert client.get('/smart/session-status/' + reference, headers=bearer(service)).json()['active'] is False
    assert client.get('/fhir/Patient/' + patient, headers=bearer(access)).status_code == 401
    assert client.get('/int/census').status_code == 401
    assert client.post('/auth/login', json={'username': 'nurse.lopez', 'password': 'local-test-password-456'}).status_code == 200
    csrf = client.cookies.get('mt_csrf')
    assert client.post('/int/ip/IP2610-0142/manual-summary', headers={'X-CSRF-Token': csrf}, json={'text': 'test'}).status_code == 403
    assert client.get('/int/mis/discharge-tat').status_code == 200
    local_login.set_password('nurse.lopez', 'another-local-test-password')
    assert client.get('/auth/session').status_code == 401
    assert client.post('/auth/login', json={'username': 'nurse.lopez', 'password': 'local-test-password-456'}).status_code == 401
    assert client.post('/auth/login', json={'username': 'nurse.lopez', 'password': 'another-local-test-password'}).status_code == 200
    client.cookies.clear()
