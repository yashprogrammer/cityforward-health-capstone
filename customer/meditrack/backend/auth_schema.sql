-- Additive migration: do not use the destructive legacy seed schema for identity.
-- One sign-in identity (an Entra object ID) may be linked to several staff accounts, for example a clinician who is also a
-- department administrator. An administrator creates each link; the person can switch between their linked accounts.
CREATE TABLE IF NOT EXISTS staff_identity (
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    user_id TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    linked_by TEXT,
    PRIMARY KEY (issuer, subject, user_id)
);
ALTER TABLE staff_identity ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE staff_identity ADD COLUMN IF NOT EXISTS linked_by TEXT;
CREATE TABLE IF NOT EXISTS staff_session (
    session_hash TEXT PRIMARY KEY,
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    user_id TEXT NOT NULL,
    csrf_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    FOREIGN KEY (issuer, subject, user_id) REFERENCES staff_identity (issuer, subject, user_id)
);
-- Databases created before multi-account links keyed identities by (issuer, subject) alone: widen the key and the session link.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'staff_identity'::regclass AND contype = 'p' AND array_length(conkey, 1) = 2) THEN
        ALTER TABLE staff_session DROP CONSTRAINT IF EXISTS staff_session_issuer_subject_fkey;
        ALTER TABLE staff_identity DROP CONSTRAINT staff_identity_pkey;
        ALTER TABLE staff_identity ADD PRIMARY KEY (issuer, subject, user_id);
        ALTER TABLE staff_session ADD CONSTRAINT staff_session_identity_fkey
            FOREIGN KEY (issuer, subject, user_id) REFERENCES staff_identity (issuer, subject, user_id);
    END IF;
END $$;
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

-- A valid Microsoft sign-in with no staff account lands here so an administrator can assign it, instead of editing tables by hand.
CREATE TABLE IF NOT EXISTS staff_access_request (
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    display_name TEXT,
    email TEXT,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'PENDING',
    PRIMARY KEY (issuer, subject)
);
