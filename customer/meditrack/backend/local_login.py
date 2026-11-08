"""Local teaching login. Accounts use passwords set by the local operator.

This is limited to the loopback demo; it is not a hospital identity provider.
"""
import argparse
import getpass
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import staff_sessions
from .db import audit, close_pool, connect, row

router = APIRouter(prefix='/auth')
_attempts = defaultdict(deque)
_salt = '00' * 16  # equal-cost verification for unknown accounts


def _password_hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def set_password(user_id: str, password: str) -> None:
    if os.getenv('APP_ENV') != 'local' or os.getenv('AUTH_MODE') != 'local':
        raise ValueError('Local passwords can only be configured in local mode')
    if len(password) < 12 or len(password) > 256:
        raise ValueError('Choose a password between 12 and 256 characters')
    salt = secrets.token_hex(16)
    hashed = _password_hash(password, salt)
    staff_sessions.migrate()
    with connect() as c:
        if not row(c, 'SELECT USR_ID FROM USR_MST WHERE USR_ID=%s AND IS_ACTV=1', user_id):
            raise ValueError('Staff ID does not exist or is disabled')
        c.execute("INSERT INTO staff_identity (issuer, subject, user_id) VALUES ('local',%s,%s) ON CONFLICT (issuer,subject,user_id) DO UPDATE SET active=true", (user_id, user_id))
        c.execute('INSERT INTO local_staff_password VALUES (%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET salt=EXCLUDED.salt, password_hash=EXCLUDED.password_hash', (user_id, salt, hashed))
        c.execute("UPDATE staff_session SET revoked_at=now() WHERE issuer='local' AND subject=%s AND revoked_at IS NULL", (user_id,))
        audit(c, 'LOCAL_OPERATOR', 'STAFF_PASSWORD_SET', user_id)


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


@router.post('/login')
def login(body: Login, request: Request):
    if os.getenv('AUTH_MODE') != 'local':
        raise HTTPException(404, 'Not found')
    origin = request.headers.get('origin')
    expected = os.getenv('PUBLIC_BASE_URL', 'http://localhost:8001').rstrip('/')
    if origin and origin.rstrip('/') != expected:
        raise HTTPException(403, 'Invalid request origin')
    key = request.client.host if request.client else 'unknown'
    attempts = _attempts[key]
    now = time.monotonic()
    while attempts and attempts[0] < now - 300:
        attempts.popleft()
    if len(attempts) >= 10:
        raise HTTPException(429, 'Too many attempts. Try again later.')
    with connect() as c:
        record = row(c, 'SELECT p.salt, p.password_hash FROM local_staff_password p JOIN USR_MST u ON u.USR_ID=p.user_id WHERE p.user_id=%s AND u.IS_ACTV=1', body.username)
    candidate = _password_hash(body.password, record['SALT'] if record else _salt)
    if not record or not hmac.compare_digest(candidate, record['PASSWORD_HASH']):
        attempts.append(now)
        raise HTTPException(401, 'Incorrect staff ID or password')
    attempts.clear()
    result = staff_sessions.create_session('local', body.username, request.cookies.get('mt_session'))
    if not result:
        raise HTTPException(401, 'Incorrect staff ID or password')
    token, csrf = result
    response = JSONResponse({'ok': True}, headers={'Cache-Control': 'no-store'})
    response.set_cookie('mt_session', token, httponly=True, samesite='strict', max_age=28800, path='/')
    response.set_cookie('mt_csrf', csrf, httponly=False, samesite='strict', max_age=28800, path='/')
    return response


@router.get('/session')
def session(request: Request):
    staff = staff_sessions.get_session(request.cookies.get('mt_session', ''))
    if not staff:
        raise HTTPException(401, 'Staff session is missing or expired')
    accounts = staff_sessions.linked_accounts(staff['ISSUER'], staff['SUBJECT'])
    return JSONResponse({'user': staff['USR_ID'], 'name': staff['USR_NM'], 'role': staff['ROLE_CD'], 'unit': staff['UNIT_CD'],
                         'accounts': [{'user': a['USR_ID'], 'name': a['USR_NM'], 'role': a['ROLE_CD'], 'unit': a['UNIT_CD']} for a in accounts] if len(accounts) > 1 else []},
                        headers={'Cache-Control': 'no-store'})


class Switch(BaseModel):
    user: str = Field(min_length=1, max_length=64)


@router.post('/switch')
def switch(body: Switch, request: Request, x_csrf_token: str = Header(default='')):
    """Change to another staff account an administrator linked to the same sign-in. The old session ends, so SMART tokens issued to it stop working."""
    token = request.cookies.get('mt_session', '')
    result = staff_sessions.switch_account(token, x_csrf_token, body.user) if x_csrf_token else None
    if not result:
        raise HTTPException(403, 'That account is not linked to your sign-in, or the session is invalid')
    new_token, csrf = result
    response = JSONResponse({'ok': True}, headers={'Cache-Control': 'no-store'})
    secure = os.getenv('AUTH_MODE') == 'oidc'
    response.set_cookie('mt_session', new_token, httponly=True, secure=secure, samesite='lax' if secure else 'strict', max_age=28800, path='/')
    response.set_cookie('mt_csrf', csrf, httponly=False, secure=secure, samesite='lax' if secure else 'strict', max_age=28800, path='/')
    return response


@router.post('/logout')
def logout(request: Request, x_csrf_token: str = Header(default='')):
    token = request.cookies.get('mt_session', '')
    if not x_csrf_token or not staff_sessions.get_session(token, csrf=x_csrf_token):
        raise HTTPException(403, 'Invalid session or CSRF token')
    staff_sessions.revoke_session(token, audit_logout=True)
    response = JSONResponse({'ok': True}, headers={'Cache-Control': 'no-store'})
    response.delete_cookie('mt_session', path='/')
    response.delete_cookie('mt_csrf', path='/')
    return response


def main():
    parser = argparse.ArgumentParser(description='Set a local teaching account password')
    parser.add_argument('staff_id', nargs='+')
    args = parser.parse_args()
    try:
        for staff_id in args.staff_id:
            first = getpass.getpass(f'New password for {staff_id}: ')
            second = getpass.getpass('Repeat password: ')
            if first != second:
                parser.error('Passwords do not match')
            set_password(staff_id, first)
            print(f'Password set for {staff_id}.')
    finally:
        close_pool()


if __name__ == '__main__':
    main()
