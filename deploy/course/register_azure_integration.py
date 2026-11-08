"""Add only this course integration to an existing synthetic EHR database.

Run once from the setup container. It refuses to replace existing registrations.
The only key it reads is the AI backend's public JWKS.
"""
import json
import os
from pathlib import Path

from meditrack.backend import staff_sessions
from meditrack.backend.db import connect, now, row

base = os.environ['INTEGRATED_BASE_URL'].rstrip('/')
issuer = f"https://login.microsoftonline.com/{os.environ['ENTRA_TENANT_ID']}/v2.0"
subject = os.environ['ENTRA_USER_OID']
webhook_secret = os.environ['INTEGRATED_WEBHOOK_SECRET']
jwks = json.loads(Path('/keys/ai-backend.jwks.json').read_text())
staff_sessions.migrate()

with connect() as c:
    staff = row(c, 'SELECT USR_ID FROM USR_MST WHERE USR_ID=%s AND IS_ACTV=1', 'attending.raman')
    if not staff:
        raise RuntimeError('The assigned synthetic attending account is not active')
    # An identity can be linked to several accounts (staff dashboard); this script only guarantees the first one.
    mapping = row(c, 'SELECT ACTIVE FROM staff_identity WHERE issuer=%s AND subject=%s AND user_id=%s', issuer, subject, 'attending.raman')
    if mapping and not mapping['ACTIVE']:
        raise RuntimeError('The Entra identity already has an inactive link to this account')
    if not mapping:
        c.execute('INSERT INTO staff_identity (issuer,subject,user_id) VALUES (%s,%s,%s)', (issuer, subject, 'attending.raman'))

    registrations = [
        ('cityforward-ai-dashboard', 'CityForward AI clinical dashboard', 'PUBLIC', base + '/ai/auth/callback', None,
         'launch openid fhirUser patient/*.read patient/DocumentReference.write user/Practitioner.read'),
        ('cityforward-ai-backend', 'CityForward AI backend service', 'BACKEND', '', json.dumps(jwks),
         'system/*.read system/DocumentReference.write'),
    ]
    for client_id, name, typ, redirects, keys, scopes in registrations:
        old = row(c, 'SELECT CLIENT_TYP, REDIRECT_URIS, JWKS, SCOPES, IS_ACTV FROM SMT_CLIENT WHERE CLIENT_ID=%s', client_id)
        if old:
            same_keys = json.loads(old['JWKS']) == jwks if keys and old['JWKS'] else old['JWKS'] is None and keys is None
            if (old['CLIENT_TYP'] != typ or (old['REDIRECT_URIS'] or '') != redirects
                    or old['SCOPES'] != scopes or not old['IS_ACTV'] or not same_keys):
                raise RuntimeError(f'Refusing to overwrite existing SMART client {client_id}')
            continue
        c.execute('INSERT INTO SMT_CLIENT VALUES (%s,%s,%s,%s,%s,%s,1,%s)',
                  (client_id, name, typ, redirects, keys, scopes, now()))

    hook_name = 'CityForward AI integrated course'
    hook_url = 'http://ai-integrated:8080/webhooks/meditrack'
    hook = row(c, 'SELECT WH_URL, WH_SECRET, EVENTS, IS_ACTV FROM SYS_WEBHOOK WHERE WH_NM=%s', hook_name)
    if hook and (hook['WH_URL'] != hook_url or hook['WH_SECRET'] != webhook_secret or hook['EVENTS'] != '*' or not hook['IS_ACTV']):
        raise RuntimeError('Refusing to overwrite an existing webhook')
    if not hook:
        c.execute("INSERT INTO SYS_WEBHOOK (WH_NM,WH_URL,WH_SECRET,EVENTS,IS_ACTV,CRT_BY,CRT_DT) VALUES (%s,%s,%s,'*',1,'it.admin',%s)",
                  (hook_name, hook_url, webhook_secret, now()))
print('Registered the assigned demo staff identity, SMART clients and event subscription.')
