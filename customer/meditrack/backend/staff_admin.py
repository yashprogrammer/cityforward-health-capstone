"""Staff and access administration (System Admin, IT_ADMIN only; routes are allow-listed in staff_guard).

Microsoft Entra proves who someone is; this module is where an administrator decides which MediTrack staff accounts that person
may use. Staff accounts live in USR_MST, links between a sign-in identity and a staff account in staff_identity. Nothing here
needs SQL: a valid sign-in with no account is kept as an access request that an administrator assigns with one click.
"""
import os
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import staff_sessions
from .db import audit, connect, row, rows

router = APIRouter(prefix='/int/admin')

ROLES = ('ATTENDING', 'RESIDENT', 'NURSE', 'HIM_CODER', 'PA_COORD', 'IT_ADMIN', 'COMPLIANCE')
UNIT_ROLES = ('ATTENDING', 'RESIDENT', 'NURSE')      # these work on one ward; every other role is hospital-wide ("ALL")
CLINICIANS = ('ATTENDING', 'RESIDENT')               # these may point at a practitioner record (the FHIR fhirUser)
# A new Staff ID reads {role prefix}.{name}: the prefix comes from the role (as in attending.raman, res.etran, nurse.lopez), the admin adds the name.
ROLE_PREFIX = {'ATTENDING': 'attending', 'RESIDENT': 'res', 'NURSE': 'nurse', 'HIM_CODER': 'him', 'PA_COORD': 'pa', 'IT_ADMIN': 'it', 'COMPLIANCE': 'compliance'}
_STAFF_ID = re.compile(r'^[a-z][a-z0-9._-]{2,31}$')
_OBJECT_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def _entra_issuer() -> str | None:
    tenant = os.getenv('ENTRA_TENANT_ID')
    return f'https://login.microsoftonline.com/{tenant}/v2.0' if tenant and os.getenv('AUTH_MODE') == 'oidc' else None


def record_access_request(issuer: str, subject: str, name: str | None, email: str | None) -> None:
    """Remember a verified sign-in that has no staff account. Called from the sign-in callback; a failure there must never block it."""
    with connect() as c:
        c.execute('''INSERT INTO staff_access_request (issuer, subject, display_name, email) VALUES (%s,%s,%s,%s)
                     ON CONFLICT (issuer, subject) DO UPDATE SET display_name=EXCLUDED.display_name, email=EXCLUDED.email,
                         last_seen=now(), attempts=staff_access_request.attempts+1, status='PENDING' ''',
                  (issuer, subject, (name or '')[:200] or None, (email or '')[:200] or None))


def _me(request: Request) -> dict:
    staff = getattr(request.state, 'staff', None)
    if not staff:      # demo mode has no staff sessions, so there is nobody to administer as
        raise HTTPException(404, 'Staff administration needs a secured sign-in (AUTH_MODE local or oidc)')
    return staff


def _units(c) -> list[str]:
    found = {r['U'] for r in c.execute('''SELECT WRD_CD AS U FROM IP_ADM_DTL UNION SELECT UNIT_CD FROM USR_MST''').fetchall() if r['U'] and r['U'] != 'ALL'}
    return sorted(found)


def _check_profile(c, role: str, unit: str, dr_id: str | None) -> tuple[str, str | None]:
    """Role decides the unit rule, so a nurse can never be saved without a ward and a coder always sees the whole hospital."""
    if role in UNIT_ROLES:
        if unit not in _units(c):
            raise HTTPException(400, f'{role.title()} accounts need a ward: choose one of {", ".join(_units(c))}')
    else:
        unit = 'ALL'
    if dr_id:
        if role not in CLINICIANS:
            raise HTTPException(400, 'Only attendings and residents link to a practitioner record')
        if not row(c, 'SELECT 1 AS X FROM DR_MST WHERE DR_ID=%s', dr_id):
            raise HTTPException(400, 'Unknown practitioner record')
    return unit, (dr_id or None)


def _another_admin(c, user_id: str) -> bool:
    return bool(row(c, "SELECT 1 AS X FROM USR_MST WHERE ROLE_CD='IT_ADMIN' AND IS_ACTV=1 AND USR_ID<>%s", user_id))


def _staff(c, user_id: str) -> dict:
    found = row(c, 'SELECT * FROM USR_MST WHERE USR_ID=%s', user_id)
    if not found:
        raise HTTPException(404, 'No such staff account')
    return found


def _link(c, issuer: str, subject: str, user_id: str, by: str) -> None:
    staff = _staff(c, user_id)
    if not staff['IS_ACTV']:
        raise HTTPException(409, 'That staff account is deactivated; reactivate it before linking a sign-in')
    c.execute('''INSERT INTO staff_identity (issuer, subject, user_id, linked_by) VALUES (%s,%s,%s,%s)
                 ON CONFLICT (issuer, subject, user_id) DO UPDATE SET active=true, linked_by=EXCLUDED.linked_by''', (issuer, subject, user_id, by))
    c.execute("UPDATE staff_access_request SET status='ASSIGNED' WHERE issuer=%s AND subject=%s", (issuer, subject))
    audit(c, by, 'IDENTITY_LINKED', f'{user_id} <- {subject}')


# ---------------------------------------------------------------- staff directory
@router.get('/staff')
def list_staff(request: Request):
    with connect() as c:
        staff = rows(c, 'SELECT USR_ID, USR_NM, ROLE_CD, UNIT_CD, DR_ID, IS_ACTV FROM USR_MST ORDER BY IS_ACTV DESC, ROLE_CD, USR_ID')
        links = rows(c, 'SELECT issuer, subject, user_id, active, created_at, linked_by FROM staff_identity ORDER BY created_at')
        seen = {r['USER_ID']: r for r in rows(c, '''SELECT user_id, max(created_at) AS last_login,
                       count(*) FILTER (WHERE revoked_at IS NULL AND last_seen > now() - interval '900 seconds' AND created_at > now() - interval '28800 seconds') AS open_sessions
                       FROM staff_session GROUP BY user_id''')}
        practitioners = rows(c, 'SELECT DR_ID, DR_NM, DR_SPCL FROM DR_MST ORDER BY DR_NM')
        units = _units(c)
    by_user: dict[str, list] = {}
    for link in links:
        by_user.setdefault(link['USER_ID'], []).append({k: v for k, v in link.items() if k != 'USER_ID'})
    for s in staff:
        s['IDENTITIES'] = by_user.get(s['USR_ID'], [])
        s['LAST_LOGIN'] = seen.get(s['USR_ID'], {}).get('LAST_LOGIN')
        s['OPEN_SESSIONS'] = seen.get(s['USR_ID'], {}).get('OPEN_SESSIONS', 0)
    return {'staff': staff, 'roles': ROLES, 'unit_roles': UNIT_ROLES, 'clinician_roles': CLINICIANS, 'role_prefix': ROLE_PREFIX, 'units': units, 'practitioners': practitioners,
            'entra_enabled': _entra_issuer() is not None, 'me': _me(request)['USR_ID']}


class StaffIn(BaseModel):
    user_id: str
    name: str = Field(min_length=3, max_length=80)
    role: Literal['ATTENDING', 'RESIDENT', 'NURSE', 'HIM_CODER', 'PA_COORD', 'IT_ADMIN', 'COMPLIANCE']
    unit: str = 'ALL'
    dr_id: str | None = None
    subject: str | None = None      # optional Entra object ID, linked in the same step


@router.post('/staff')
def create_staff(body: StaffIn, request: Request):
    user_id = body.user_id.strip().lower()
    prefix = ROLE_PREFIX[body.role] + '.'
    if not user_id.startswith(prefix) or len(user_id) == len(prefix):
        raise HTTPException(400, f'A {body.role.replace("_", " ").title()} Staff ID starts with "{prefix}" followed by a name or number, for example {prefix}smith')
    if not _STAFF_ID.fullmatch(user_id):
        raise HTTPException(400, 'Staff ID: 3 to 32 characters, lowercase letters, digits, dot, dash or underscore, starting with a letter')
    subject = (body.subject or '').strip().lower() or None
    if subject:      # checked before anything is saved, so a bad object ID never leaves a half-created account
        if not _entra_issuer():
            raise HTTPException(400, 'Microsoft sign-in is not enabled on this system')
        if not _OBJECT_ID.fullmatch(subject):
            raise HTTPException(400, 'Enter the Entra object ID: 8-4-4-4-12 letters and digits, as shown in the Entra admin center')
    me = _me(request)
    with connect() as c:
        if row(c, 'SELECT 1 AS X FROM USR_MST WHERE USR_ID=%s', user_id):
            raise HTTPException(409, 'That staff ID already exists')
        unit, dr_id = _check_profile(c, body.role, body.unit, body.dr_id)
        c.execute('INSERT INTO USR_MST VALUES (%s,%s,%s,%s,%s,1)', (user_id, body.name.strip(), body.role, unit, dr_id))
        audit(c, me['USR_ID'], 'STAFF_CREATED', f'{user_id} {body.role} {unit}')
        if subject:
            _link(c, _entra_issuer(), subject, user_id, me['USR_ID'])
    return {'ok': True, 'user_id': user_id}


class StaffUpdate(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    role: Literal['ATTENDING', 'RESIDENT', 'NURSE', 'HIM_CODER', 'PA_COORD', 'IT_ADMIN', 'COMPLIANCE']
    unit: str = 'ALL'
    dr_id: str | None = None


@router.post('/staff/{user_id}/update')
def update_staff(user_id: str, body: StaffUpdate, request: Request):
    me = _me(request)
    with connect() as c:
        before = _staff(c, user_id)
        if before['ROLE_CD'] != body.role:
            if user_id == me['USR_ID']:
                raise HTTPException(409, 'You cannot change your own role; ask another administrator')
            if before['ROLE_CD'] == 'IT_ADMIN' and before['IS_ACTV'] and not _another_admin(c, user_id):
                raise HTTPException(409, 'This is the only active administrator; make another account an administrator first')
        unit, dr_id = _check_profile(c, body.role, body.unit, body.dr_id)
        c.execute('UPDATE USR_MST SET USR_NM=%s, ROLE_CD=%s, UNIT_CD=%s, DR_ID=%s WHERE USR_ID=%s', (body.name.strip(), body.role, unit, dr_id, user_id))
        changed = [f'{k} {a} -> {b}' for k, a, b in (('role', before['ROLE_CD'], body.role), ('unit', before['UNIT_CD'], unit), ('name', before['USR_NM'], body.name.strip())) if a != b]
        audit(c, me['USR_ID'], 'STAFF_UPDATED', f'{user_id}: ' + ('; '.join(changed) or 'no change'))
    return {'ok': True}


class ActiveIn(BaseModel):
    active: bool


@router.post('/staff/{user_id}/active')
def set_active(user_id: str, body: ActiveIn, request: Request):
    me = _me(request)
    with connect() as c:
        staff = _staff(c, user_id)
        if not body.active:
            if user_id == me['USR_ID']:
                raise HTTPException(409, 'You cannot deactivate the account you are signed in with')
            if staff['ROLE_CD'] == 'IT_ADMIN' and staff['IS_ACTV'] and not _another_admin(c, user_id):
                raise HTTPException(409, 'This is the only active administrator; make another account an administrator first')
        c.execute('UPDATE USR_MST SET IS_ACTV=%s WHERE USR_ID=%s', (1 if body.active else 0, user_id))
        ended = 0
        if not body.active:      # access ends now, not at the next idle timeout
            ended = c.execute('UPDATE staff_session SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL', (user_id,)).rowcount
        audit(c, me['USR_ID'], 'STAFF_REACTIVATED' if body.active else 'STAFF_DEACTIVATED', f'{user_id}' + (f' ({ended} session(s) ended)' if ended else ''))
    return {'ok': True}


# ---------------------------------------------------------------- sign-in links
class LinkIn(BaseModel):
    subject: str


@router.post('/staff/{user_id}/links')
def add_link(user_id: str, body: LinkIn, request: Request):
    issuer = _entra_issuer()
    if not issuer:
        raise HTTPException(400, 'Microsoft sign-in is not enabled on this system')
    subject = body.subject.strip().lower()
    if not _OBJECT_ID.fullmatch(subject):
        raise HTTPException(400, 'Enter the Entra object ID: 8-4-4-4-12 letters and digits, as shown in the Entra admin center')
    with connect() as c:
        _link(c, issuer, subject, user_id, _me(request)['USR_ID'])
    return {'ok': True}


class LinkRef(BaseModel):
    issuer: str
    subject: str
    user_id: str


@router.post('/links/{action}')
def change_link(action: Literal['enable', 'disable', 'remove'], body: LinkRef, request: Request):
    me = _me(request)
    if action != 'enable' and (body.issuer, body.subject, body.user_id) == (me['ISSUER'], me['SUBJECT'], me['USR_ID']):
        raise HTTPException(409, 'You cannot disable or remove the sign-in link you are using right now')
    with connect() as c:
        if action == 'remove':
            # sessions reference the link, so end them first, then drop the link itself
            c.execute('UPDATE staff_session SET revoked_at=now() WHERE issuer=%s AND subject=%s AND user_id=%s AND revoked_at IS NULL', (body.issuer, body.subject, body.user_id))
            gone = c.execute('DELETE FROM staff_identity WHERE issuer=%s AND subject=%s AND user_id=%s AND NOT EXISTS (SELECT 1 FROM staff_session s WHERE s.issuer=%s AND s.subject=%s AND s.user_id=%s)',
                             (body.issuer, body.subject, body.user_id, body.issuer, body.subject, body.user_id)).rowcount
            if not gone:       # past sessions are kept as the audit record, so the link is switched off instead of deleted
                c.execute('UPDATE staff_identity SET active=false WHERE issuer=%s AND subject=%s AND user_id=%s', (body.issuer, body.subject, body.user_id))
        else:
            changed = c.execute('UPDATE staff_identity SET active=%s WHERE issuer=%s AND subject=%s AND user_id=%s',
                                (action == 'enable', body.issuer, body.subject, body.user_id)).rowcount
            if not changed:
                raise HTTPException(404, 'No such sign-in link')
            if action == 'disable':
                c.execute('UPDATE staff_session SET revoked_at=now() WHERE issuer=%s AND subject=%s AND user_id=%s AND revoked_at IS NULL', (body.issuer, body.subject, body.user_id))
        audit(c, me['USR_ID'], f'IDENTITY_{action.upper()}D' if action != 'remove' else 'IDENTITY_REMOVED', f'{body.user_id} / {body.subject}')
    return {'ok': True}


# ---------------------------------------------------------------- access requests
@router.get('/access-requests')
def access_requests():
    with connect() as c:
        return rows(c, '''SELECT issuer, subject, display_name, email, first_seen, last_seen, attempts, status FROM staff_access_request
                          WHERE status IN ('PENDING','DISMISSED') ORDER BY (status='PENDING') DESC, last_seen DESC LIMIT 100''')


class AssignIn(BaseModel):
    issuer: str
    subject: str
    user_id: str


@router.post('/access-requests/assign')
def assign_request(body: AssignIn, request: Request):
    with connect() as c:
        if not row(c, "SELECT 1 AS X FROM staff_access_request WHERE issuer=%s AND subject=%s AND status IN ('PENDING','DISMISSED')", body.issuer, body.subject):
            raise HTTPException(404, 'No such open access request')
        _link(c, body.issuer, body.subject, body.user_id, _me(request)['USR_ID'])
    return {'ok': True}


class DismissIn(BaseModel):
    issuer: str
    subject: str


@router.post('/access-requests/dismiss')
def dismiss_request(body: DismissIn, request: Request):
    with connect() as c:
        done = c.execute("UPDATE staff_access_request SET status='DISMISSED' WHERE issuer=%s AND subject=%s AND status='PENDING'", (body.issuer, body.subject)).rowcount
        if not done:
            raise HTTPException(404, 'No such pending access request')
        audit(c, _me(request)['USR_ID'], 'ACCESS_REQUEST_DISMISSED', body.subject)
    return {'ok': True}


# ---------------------------------------------------------------- open sessions
@router.get('/sessions')
def open_sessions(request: Request):
    mine = staff_sessions._digest(request.cookies.get('mt_session', ''))[:12]
    with connect() as c:
        found = rows(c, '''SELECT left(s.session_hash, 12) AS ID, s.user_id, u.USR_NM, u.ROLE_CD, s.issuer, s.created_at, s.last_seen
                           FROM staff_session s JOIN USR_MST u ON u.USR_ID=s.user_id
                           WHERE s.revoked_at IS NULL AND s.last_seen > now() - interval '900 seconds' AND s.created_at > now() - interval '28800 seconds'
                           ORDER BY s.last_seen DESC''')
    for s in found:
        s['CURRENT'] = s['ID'] == mine
    return found


class RevokeIn(BaseModel):
    id: str = Field(min_length=12, max_length=12, pattern='^[0-9a-f]{12}$')


@router.post('/sessions/revoke')
def revoke_session(body: RevokeIn, request: Request):
    me = _me(request)
    if staff_sessions._digest(request.cookies.get('mt_session', ''))[:12] == body.id:
        raise HTTPException(409, 'That is your own session; use Log out')
    with connect() as c:
        ended = row(c, 'UPDATE staff_session SET revoked_at=now() WHERE left(session_hash, 12)=%s AND revoked_at IS NULL RETURNING user_id', body.id)
        if not ended:
            raise HTTPException(404, 'No such open session')
        audit(c, me['USR_ID'], 'SESSION_REVOKED', ended['USER_ID'])
    return {'ok': True}
