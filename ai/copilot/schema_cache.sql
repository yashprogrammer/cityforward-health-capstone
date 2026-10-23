-- Result cache: an unchanged chart does not need a second model call. Key = hash(prompt version, model, the exact evidence). Row-level security applies (see schema_rls.sql).
CREATE TABLE IF NOT EXISTS result_cache (
    key         TEXT PRIMARY KEY,
    feature     TEXT NOT NULL,
    patient_id  TEXT NOT NULL,
    unit        TEXT,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    hits        INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS result_cache_patient ON result_cache (patient_id);
