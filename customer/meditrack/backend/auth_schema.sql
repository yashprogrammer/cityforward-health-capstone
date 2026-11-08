-- Additive migration: do not use the destructive legacy seed schema for identity.
CREATE TABLE IF NOT EXISTS staff_identity (
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    user_id TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (issuer, subject)
);
CREATE TABLE IF NOT EXISTS staff_session (
    session_hash TEXT PRIMARY KEY,
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    user_id TEXT NOT NULL,
    csrf_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    FOREIGN KEY (issuer, subject) REFERENCES staff_identity (issuer, subject)
);
-- Existing experimental sessions did not bind the internal user. Invalidate
-- them rather than assigning an identity after the fact.
ALTER TABLE staff_session ADD COLUMN IF NOT EXISTS user_id TEXT;
UPDATE staff_session SET revoked_at=now() WHERE user_id IS NULL AND revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS staff_session_identity ON staff_session (issuer, subject);

-- One-time server-side OIDC transactions; the browser holds only a random cookie.
CREATE TABLE IF NOT EXISTS staff_oidc_flow (
    flow_hash TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS local_staff_password (
    user_id TEXT PRIMARY KEY,
    salt TEXT NOT NULL,
    password_hash TEXT NOT NULL
);

-- SMART launch table belongs to the legacy schema; guard absent tables for
-- independent session-store tests and preserve existing synthetic data.
ALTER TABLE IF EXISTS SMT_LAUNCH ADD COLUMN IF NOT EXISTS session_hash TEXT;
