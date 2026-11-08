"""Database-backed staff sessions. Only token hashes are stored in PostgreSQL."""
import hashlib
import hmac
import re
import secrets
from pathlib import Path

from .db import audit, connect, row

SCHEMA = Path(__file__).with_name('auth_schema.sql')


def migrate() -> None:
    """Add only authentication tables; never invoke the destructive demo seed."""
    with connect() as c:
        c.execute(SCHEMA.read_text())


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def session_reference(token: str) -> str:
    """A non-cookie identifier for a future signed SMART token; never the session credential."""
    if not token:
        raise ValueError('session token is required')
    return _digest(token)


def create_session(issuer: str, subject: str, previous_token: str | None = None, user_id: str | None = None, switched_from: str | None = None) -> tuple[str, str] | None:
    """Create a new opaque session for an explicitly linked, active staff account.

    An identity linked to several accounts signs in as the one it used last (or `user_id` when given, which is how switching works)."""
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with connect() as c:
        staff = row(c, '''SELECT u.USR_ID FROM staff_identity i JOIN USR_MST u ON u.USR_ID=i.user_id
                          WHERE i.issuer=%s AND i.subject=%s AND i.active AND u.IS_ACTV=1 AND (%s::text IS NULL OR i.user_id=%s)
                          ORDER BY (SELECT max(s.created_at) FROM staff_session s WHERE s.issuer=i.issuer AND s.subject=i.subject AND s.user_id=i.user_id) DESC NULLS LAST, i.user_id
                          LIMIT 1 FOR UPDATE OF i''', issuer, subject, user_id, user_id)
        if not staff:
            return None
        if previous_token:
            c.execute('UPDATE staff_session SET revoked_at=now() WHERE session_hash=%s AND revoked_at IS NULL', (_digest(previous_token),))
        c.execute('INSERT INTO staff_session (session_hash, issuer, subject, user_id, csrf_hash) VALUES (%s,%s,%s,%s,%s)',
                  (_digest(token), issuer, subject, staff['USR_ID'], _digest(csrf)))
        if switched_from:
            audit(c, staff['USR_ID'], 'STAFF_ACCOUNT_SWITCH', f'{switched_from} -> {staff["USR_ID"]}')
        else:
            audit(c, staff['USR_ID'], 'STAFF_LOGIN', 'LOCAL' if issuer == 'local' else 'OIDC')
    return token, csrf


def linked_accounts(issuer: str, subject: str) -> list[dict]:
    """The active staff accounts an administrator has linked to this sign-in identity."""
    with connect() as c:
        return c.execute('''SELECT u.USR_ID, u.USR_NM, u.ROLE_CD, u.UNIT_CD FROM staff_identity i JOIN USR_MST u ON u.USR_ID=i.user_id
                           WHERE i.issuer=%s AND i.subject=%s AND i.active AND u.IS_ACTV=1 ORDER BY u.USR_NM''', (issuer, subject)).fetchall()


def switch_account(token: str, csrf: str, user_id: str) -> tuple[str, str] | None:
    """Move a signed-in person to another account linked to the same identity. The old session ends, so tokens issued to it stop working."""
    staff = get_session(token, csrf=csrf)
    if not staff or staff['USR_ID'] == user_id:
        return None
    return create_session(staff['ISSUER'], staff['SUBJECT'], token, user_id=user_id, switched_from=staff['USR_ID'])


def get_session(token: str, *, idle_seconds: int = 900, absolute_seconds: int = 28800, csrf: str | None = None) -> dict | None:
    """Check expiry, revocation and staff status on every request, then refresh idle time."""
    if not token or idle_seconds <= 0 or absolute_seconds <= 0:
        return None
    with connect() as c:
        staff = row(c, '''SELECT u.*, s.csrf_hash, s.issuer AS ISSUER, s.subject AS SUBJECT FROM staff_session s
                          JOIN staff_identity i ON i.issuer=s.issuer AND i.subject=s.subject
                          JOIN USR_MST u ON u.USR_ID=i.user_id AND u.USR_ID=s.user_id
                          WHERE s.session_hash=%s AND s.revoked_at IS NULL AND i.active AND u.IS_ACTV=1
                            AND s.last_seen > now() - (%s * interval '1 second')
                            AND s.created_at > now() - (%s * interval '1 second') FOR UPDATE OF s''',
                    _digest(token), idle_seconds, absolute_seconds)
        if not staff or (csrf is not None and not hmac.compare_digest(staff['CSRF_HASH'], _digest(csrf))):
            return None
        c.execute('UPDATE staff_session SET last_seen=now() WHERE session_hash=%s', (_digest(token),))
        staff.pop('CSRF_HASH', None)
        return staff


def revoke_session(token: str, *, audit_logout: bool = False) -> None:
    if token:
        with connect() as c:
            changed = row(c, 'UPDATE staff_session SET revoked_at=now() WHERE session_hash=%s AND revoked_at IS NULL RETURNING user_id', _digest(token))
        # Revocation commits before audit so an audit failure cannot restore a
        # session after the user asked to log out.
        if audit_logout and changed:
            with connect() as c:
                audit(c, changed['USER_ID'], 'STAFF_LOGOUT', 'LOCAL')


def session_status(reference: str, *, idle_seconds: int = 900, absolute_seconds: int = 28800) -> dict | None:
    """Read session identity for a future authenticated introspection path without extending idle time.

    A session reference is not itself a credential; callers must authenticate and
    authorize the service requesting this information. Do not expose this directly.
    """
    if not isinstance(reference, str) or not re.fullmatch(r'[0-9a-f]{64}', reference) or idle_seconds <= 0 or absolute_seconds <= 0:
        return None
    with connect() as c:
        return row(c, '''SELECT u.USR_ID, u.ROLE_CD, u.UNIT_CD FROM staff_session s
                         JOIN staff_identity i ON i.issuer=s.issuer AND i.subject=s.subject
                         JOIN USR_MST u ON u.USR_ID=i.user_id AND u.USR_ID=s.user_id
                         WHERE s.session_hash=%s AND s.revoked_at IS NULL AND i.active AND u.IS_ACTV=1
                           AND s.last_seen > now() - (%s * interval '1 second')
                           AND s.created_at > now() - (%s * interval '1 second')''', reference, idle_seconds, absolute_seconds)
