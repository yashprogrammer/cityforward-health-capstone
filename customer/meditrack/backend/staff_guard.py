"""Role checks and CSRF protection for local MediTrack staff sessions."""
import re

from fastapi import HTTPException, Request

from . import staff_policy, staff_sessions
from .db import connect, row


_PATIENT = re.compile(r'^/int/ip/([^/]+)(?:/(.*))?$')
_DOCUMENT = re.compile(r'^/int/docs/(\d+)$')
_ADMIN_ROUTES = (
    ('GET', re.compile(r'^/int/admin/(extlinks|smart/clients|webhooks|webhooks/outbox)$')),
    ('POST', re.compile(r'^/int/admin/(extlinks|smart/clients|webhooks)$')),
    ('POST', re.compile(r'^/int/admin/extlinks/\d+/toggle$')),
    ('POST', re.compile(r'^/int/admin/smart/clients/[^/]+/toggle$')),
    ('POST', re.compile(r'^/int/admin/webhooks/\d+/toggle$')),
    ('POST', re.compile(r'^/int/admin/webhooks/outbox/\d+/replay$')),
)


def patient_unit(ip_no: str) -> str | None:
    with connect() as c:
        found = row(c, 'SELECT WRD_CD FROM IP_ADM_DTL WHERE IP_NO=%s', ip_no)
        return found['WRD_CD'] if found else None


def document_unit(doc_id: str) -> str | None:
    with connect() as c:
        found = row(c, 'SELECT a.WRD_CD FROM PT_DOC d JOIN IP_ADM_DTL a ON a.IP_NO=d.IP_NO WHERE d.DOC_ID=%s', doc_id)
        return found['WRD_CD'] if found else None


def authorize_internal_request(request: Request) -> dict:
    """Return current staff on authorization; no unknown route is implicitly allowed."""
    csrf = request.headers.get('X-CSRF-Token', '') if request.method not in ('GET', 'HEAD', 'OPTIONS') else None
    if csrf == '':
        raise HTTPException(403, 'CSRF token required')
    staff = staff_sessions.get_session(request.cookies.get('mt_session', ''), csrf=csrf)
    if not staff:
        raise HTTPException(401, 'Staff session missing, expired, or invalid')
    request.state.staff = staff
    path, method, role = request.url.path, request.method, staff['ROLE_CD']
    unit = staff.get('UNIT_CD')
    units = [unit] if unit else []
    patient_match, doc_match = _PATIENT.fullmatch(path), _DOCUMENT.fullmatch(path)
    if patient_match:
        ip, suffix = patient_match.groups()
        if method == 'GET' and suffix in (None, 'summary-template'):
            action = 'patient.view'
        elif method == 'POST' and suffix == 'manual-summary':
            action = 'summary.save'
        elif method == 'POST' and suffix == 'discharge':
            action = 'discharge.finalize'
        elif method == 'POST' and suffix == 'discharge-advise':
            action = 'discharge.advise'
        elif method == 'POST' and suffix == 'docs':
            action = 'document.upload'
        else:
            raise HTTPException(403, 'Action is not approved for secured mode')
        if not staff_policy.may_access_patient(role, units, patient_unit(ip), action):
            raise HTTPException(403, 'Patient or action is outside your assignment')
    elif doc_match and method == 'GET':
        if not staff_policy.may_access_patient(role, units, document_unit(doc_match.group(1)), 'patient.view'):
            raise HTTPException(403, 'Patient document is outside your assignment')
    elif any(method == verb and pattern.fullmatch(path) for verb, pattern in _ADMIN_ROUTES):
        if role != 'IT_ADMIN':
            raise HTTPException(403, 'Administrator access required')
    elif path == '/int/audit' and method == 'GET':
        if role not in ('IT_ADMIN', 'COMPLIANCE'):
            raise HTTPException(403, 'Audit access required')
    elif path in ('/int/census', '/int/discharged') and method == 'GET':
        if role not in staff_policy.CLINICAL_READERS or not unit:
            raise HTTPException(403, 'Clinical assignment required')
    elif path == '/int/mis/discharge-tat' and method == 'GET':
        if role not in staff_policy.CLINICAL_READERS and role not in ('IT_ADMIN', 'COMPLIANCE'):
            raise HTTPException(403, 'Staff access required')
    else:
        # Any new endpoint needs an explicit policy.
        raise HTTPException(403, 'Action is not approved for secured mode')
    return staff
