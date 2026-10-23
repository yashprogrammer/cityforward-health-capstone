CREATE TABLE IF NOT EXISTS readiness (
    id          BIGSERIAL PRIMARY KEY,
    patient_id  TEXT NOT NULL,
    unit        TEXT,
    payload     JSONB NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS readiness_patient ON readiness (patient_id, created_at DESC);
