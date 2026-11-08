"""Staff and access administration. Run against a disposable, seeded database with APP_ENV=local AUTH_MODE=local."""
import pytest
from fastapi.testclient import TestClient

from meditrack.backend import local_login, staff_admin, staff_sessions
from meditrack.backend.app import app
from meditrack.backend.db import connect

PASSWORD = 'staff-admin-test-password'
ISSUER = 'https://issuer.test/v2.0'
OID_A = '11111111-aaaa-4bbb-8ccc-222222222222'
OID_B = '33333333-aaaa-4bbb-8ccc-444444444444'


@pytest.fixture
def admin(client, monkeypatch):
    """A signed-in IT administrator; Entra links use a fake issuer because local mode has no Microsoft sign-in."""
    monkeypatch.setattr(staff_admin, '_entra_issuer', lambda: ISSUER)
    staff_sessions.migrate()
    with connect() as c:     # auth tables outlive the seed, so start from a clean slate for the fake issuer
        c.execute('DELETE FROM staff_access_request WHERE issuer=%s', (ISSUER,))
        c.execute('DELETE FROM staff_identity WHERE issuer=%s', (ISSUER,))
    local_login.set_password('it.admin', PASSWORD)
    client.cookies.clear()
    assert client.post('/auth/login', json={'username': 'it.admin', 'password': PASSWORD}).status_code == 200
    yield client
    client.cookies.clear()


def csrf(c):
    return {'X-CSRF-Token': c.cookies.get('mt_csrf')}


def post(c, path, body=None):
    return c.post(path, headers=csrf(c), json=body or {})


def staff_row(c, user_id):
    return next(s for s in c.get('/int/admin/staff').json()['staff'] if s['USR_ID'] == user_id)


def test_only_an_administrator_can_manage_staff(client):
    client.cookies.clear()
    assert client.get('/int/admin/staff').status_code == 401
    local_login.set_password('nurse.lopez', PASSWORD)
    assert client.post('/auth/login', json={'username': 'nurse.lopez', 'password': PASSWORD}).status_code == 200
    for path in ('/int/admin/staff', '/int/admin/access-requests', '/int/admin/sessions'):
        assert client.get(path).status_code == 403
    assert post(client, '/int/admin/staff', {'user_id': 'sneaky.nurse', 'name': 'Sneaky Nurse', 'role': 'IT_ADMIN'}).status_code == 403
    assert post(client, '/int/admin/staff/nurse.lopez/active', {'active': False}).status_code == 403
    client.cookies.clear()


def test_admin_creates_edits_and_deactivates_staff(admin):
    listing = admin.get('/int/admin/staff').json()
    assert {'ATTENDING', 'NURSE', 'IT_ADMIN'} <= set(listing['roles']) and '4W-CARD' in listing['units'] and listing['me'] == 'it.admin'
    body = {'user_id': 'nurse.ames', 'name': 'Dana Ames, RN', 'role': 'NURSE', 'unit': '4W-CARD'}
    assert post(admin, '/int/admin/staff', {**body, 'unit': 'NOWHERE'}).status_code == 400          # a nurse needs a real ward
    assert post(admin, '/int/admin/staff', {**body, 'user_id': 'Bad Id!'}).status_code == 400
    assert post(admin, '/int/admin/staff', {**body, 'role': 'ASTRONAUT'}).status_code == 422
    assert post(admin, '/int/admin/staff', {**body, 'dr_id': 'DR-1'}).status_code == 400            # nurses do not link to a practitioner record
    assert post(admin, '/int/admin/staff', body).status_code == 200
    assert post(admin, '/int/admin/staff', body).status_code == 409
    assert staff_row(admin, 'nurse.ames')['UNIT_CD'] == '4W-CARD'
    # a role that works hospital-wide always saves as ALL, whatever the form sent
    assert post(admin, '/int/admin/staff/nurse.ames/update', {'name': 'Dana Ames, CCS', 'role': 'HIM_CODER', 'unit': '4W-CARD'}).status_code == 200
    assert (staff_row(admin, 'nurse.ames')['ROLE_CD'], staff_row(admin, 'nurse.ames')['UNIT_CD']) == ('HIM_CODER', 'ALL')
    practitioner = admin.get('/int/admin/staff').json()['practitioners'][0]['DR_ID']
    assert post(admin, '/int/admin/staff/nurse.ames/update', {'name': 'Dana Ames, MD', 'role': 'ATTENDING', 'unit': '4W-CARD', 'dr_id': practitioner}).status_code == 200
    assert staff_row(admin, 'nurse.ames')['DR_ID'] == practitioner

    # deactivating ends the person's open sessions immediately
    local_login.set_password('nurse.ames', PASSWORD)
    other = TestClient(app)
    assert other.post('/auth/login', json={'username': 'nurse.ames', 'password': PASSWORD}).status_code == 200
    assert other.get('/int/census').status_code == 200
    assert post(admin, '/int/admin/staff/nurse.ames/active', {'active': False}).status_code == 200
    assert other.get('/int/census').status_code == 401
    assert post(admin, '/int/admin/staff/nurse.ames/links', {'subject': OID_A}).status_code == 409   # no sign-in links to a deactivated account
    assert post(admin, '/int/admin/staff/nurse.ames/active', {'active': True}).status_code == 200
    actions = [r['ACTN'] for r in admin.get('/int/audit').json()]
    assert {'STAFF_CREATED', 'STAFF_UPDATED', 'STAFF_DEACTIVATED', 'STAFF_REACTIVATED'} <= set(actions)


def test_an_administrator_cannot_lock_themselves_out(admin):
    assert post(admin, '/int/admin/staff/it.admin/active', {'active': False}).status_code == 409
    assert post(admin, '/int/admin/staff/it.admin/update', {'name': 'Sam Whitlock (IT)', 'role': 'NURSE', 'unit': '4W-CARD'}).status_code == 409
    assert admin.get('/int/admin/staff').status_code == 200
    assert post(admin, '/int/admin/staff/missing.person/active', {'active': False}).status_code == 404


def test_sign_in_links_and_access_requests(admin):
    assert post(admin, '/int/admin/staff/nurse.park/links', {'subject': 'not-a-guid'}).status_code == 400
    assert post(admin, '/int/admin/staff/nurse.park/links', {'subject': OID_A.upper()}).status_code == 200      # stored in the form Entra sends
    link = next(i for i in staff_row(admin, 'nurse.park')['IDENTITIES'] if i['SUBJECT'] == OID_A)
    assert link['ISSUER'] == ISSUER and link['ACTIVE'] and link['LINKED_BY'] == 'it.admin'

    # an unassigned Microsoft sign-in is kept as a request, counted, and assigned with one click
    staff_admin.record_access_request(ISSUER, OID_B, 'Pat Newhire', 'pat@example.org')
    staff_admin.record_access_request(ISSUER, OID_B, 'Pat Newhire', 'pat@example.org')
    pending = [r for r in admin.get('/int/admin/access-requests').json() if r['SUBJECT'] == OID_B][0]
    assert (pending['DISPLAY_NAME'], pending['ATTEMPTS'], pending['STATUS']) == ('Pat Newhire', 2, 'PENDING')
    assert post(admin, '/int/admin/access-requests/assign', {'issuer': ISSUER, 'subject': OID_B, 'user_id': 'nurse.lopez'}).status_code == 200
    assert not [r for r in admin.get('/int/admin/access-requests').json() if r['SUBJECT'] == OID_B]
    assert any(i['SUBJECT'] == OID_B for i in staff_row(admin, 'nurse.lopez')['IDENTITIES'])
    assert post(admin, '/int/admin/access-requests/assign', {'issuer': ISSUER, 'subject': OID_B, 'user_id': 'nurse.lopez'}).status_code == 404

    # a dismissed request comes back if the person tries again
    staff_admin.record_access_request(ISSUER, '55555555-aaaa-4bbb-8ccc-666666666666', 'Casey Visitor', None)
    ref = {'issuer': ISSUER, 'subject': '55555555-aaaa-4bbb-8ccc-666666666666'}
    assert post(admin, '/int/admin/access-requests/dismiss', ref).status_code == 200
    assert next(r for r in admin.get('/int/admin/access-requests').json() if r['SUBJECT'] == ref['subject'])['STATUS'] == 'DISMISSED'
    staff_admin.record_access_request(ISSUER, ref['subject'], 'Casey Visitor', None)
    assert next(r for r in admin.get('/int/admin/access-requests').json() if r['SUBJECT'] == ref['subject'])['STATUS'] == 'PENDING'

    # a link can be switched off (ending that person's sessions), on again, and removed
    mine = {'issuer': ISSUER, 'subject': OID_A, 'user_id': 'nurse.park'}
    assert post(admin, '/int/admin/links/disable', mine).status_code == 200
    assert not next(i for i in staff_row(admin, 'nurse.park')['IDENTITIES'] if i['SUBJECT'] == OID_A)['ACTIVE']
    assert post(admin, '/int/admin/links/enable', mine).status_code == 200
    assert post(admin, '/int/admin/links/remove', mine).status_code == 200
    assert not any(i['SUBJECT'] == OID_A for i in staff_row(admin, 'nurse.park')['IDENTITIES'])
    assert post(admin, '/int/admin/links/disable', {**mine, 'subject': 'ffffffff-ffff-4fff-8fff-ffffffffffff'}).status_code == 404
    # nobody can switch off the link they are signed in with
    own = {'issuer': 'local', 'subject': 'it.admin', 'user_id': 'it.admin'}
    assert post(admin, '/int/admin/links/disable', own).status_code == 409
    assert post(admin, '/int/admin/links/remove', own).status_code == 409


def test_open_sessions_can_be_ended_by_an_administrator(admin):
    local_login.set_password('nurse.park', PASSWORD)
    other = TestClient(app)
    assert other.post('/auth/login', json={'username': 'nurse.park', 'password': PASSWORD}).status_code == 200
    sessions = admin.get('/int/admin/sessions').json()
    theirs = next(s for s in sessions if s['USER_ID'] == 'nurse.park')
    assert next(s for s in sessions if s['USER_ID'] == 'it.admin')['CURRENT'] is True
    mine = next(s for s in sessions if s['CURRENT'])
    assert post(admin, '/int/admin/sessions/revoke', {'id': mine['ID']}).status_code == 409           # ending your own session is Log out
    assert post(admin, '/int/admin/sessions/revoke', {'id': theirs['ID']}).status_code == 200
    assert other.get('/int/census').status_code == 401
    assert post(admin, '/int/admin/sessions/revoke', {'id': theirs['ID']}).status_code == 404
    assert post(admin, '/int/admin/sessions/revoke', {'id': 'nothex'}).status_code == 422


def test_a_person_linked_to_two_accounts_can_switch_between_them(admin):
    with connect() as c:     # as an administrator would link them: the same sign-in identity, a second account
        c.execute("INSERT INTO staff_identity (issuer, subject, user_id) VALUES ('local','it.admin','attending.raman') ON CONFLICT (issuer,subject,user_id) DO UPDATE SET active=true")
    old_token = admin.cookies.get('mt_session')
    session = admin.get('/auth/session').json()
    assert session['user'] == 'it.admin' and {a['user'] for a in session['accounts']} == {'it.admin', 'attending.raman'}
    assert admin.post('/auth/switch', json={'user': 'attending.raman'}).status_code == 403                  # CSRF token required
    assert post(admin, '/auth/switch', {'user': 'nurse.lopez'}).status_code == 403                         # not linked to this sign-in
    assert post(admin, '/auth/switch', {'user': 'it.admin'}).status_code == 403                            # already that account
    assert post(admin, '/auth/switch', {'user': 'attending.raman'}).status_code == 200
    assert staff_sessions.get_session(old_token) is None                                                   # the old session ended
    assert admin.get('/auth/session').json()['user'] == 'attending.raman'
    assert admin.get('/int/admin/staff').status_code == 403                                                # a clinician account has no admin rights
    assert admin.get('/int/census').status_code == 200
    assert post(admin, '/auth/switch', {'user': 'it.admin'}).status_code == 200
    assert admin.get('/int/admin/staff').status_code == 200
    audited = [r for r in admin.get('/int/audit').json() if r['ACTN'] == 'STAFF_ACCOUNT_SWITCH']
    assert {r['REF'] for r in audited} >= {'it.admin -> attending.raman', 'attending.raman -> it.admin'}
    with connect() as c:     # a deactivated account drops out of the switcher
        c.execute("UPDATE staff_identity SET active=false WHERE issuer='local' AND subject='it.admin' AND user_id='attending.raman'")
    assert admin.get('/auth/session').json()['accounts'] == []
    with connect() as c:
        c.execute("DELETE FROM staff_identity WHERE issuer='local' AND subject='it.admin' AND user_id='attending.raman' AND NOT EXISTS (SELECT 1 FROM staff_session s WHERE s.user_id='attending.raman' AND s.subject='it.admin')")
