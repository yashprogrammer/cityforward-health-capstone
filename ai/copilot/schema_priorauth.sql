CREATE TABLE IF NOT EXISTS prior_auth (
    id            TEXT PRIMARY KEY,
    patient_id    TEXT NOT NULL,
    encounter_id  TEXT NOT NULL,
    unit          TEXT,
    service_code  TEXT NOT NULL,
    service_name  TEXT NOT NULL,
    plan          TEXT,
    status        TEXT NOT NULL,          -- DRAFT | APPROVED | SUBMITTED | QUEUED | PENDED | CERTIFIED | DENIED | NOT_REQUIRED
    requirements  JSONB NOT NULL,
    diagnoses     JSONB NOT NULL DEFAULT '[]',
    letter        TEXT,
    mode          TEXT NOT NULL,          -- llm | deterministic
    model         TEXT,
    problems      JSONB NOT NULL DEFAULT '[]',
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_by   TEXT,
    approved_at   TIMESTAMPTZ,
    submitted_by  TEXT,
    submitted_at  TIMESTAMPTZ,
    claim_identifier TEXT,
    payer_ref     TEXT,                   -- ClaimResponse id at the payer
    payer_action  TEXT,                   -- X12 review action A1 | A3 | A4 | NA
    payer_notes   JSONB,
    preauth_ref   TEXT,
    history       JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS prior_auth_patient ON prior_auth (patient_id);
