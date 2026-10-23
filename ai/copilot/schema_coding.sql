CREATE TABLE IF NOT EXISTS coding_worksheet (
    id           TEXT PRIMARY KEY,
    patient_id   TEXT NOT NULL,
    encounter_id TEXT NOT NULL,
    unit         TEXT,
    status       TEXT NOT NULL,            -- OPEN | FINAL
    items        JSONB NOT NULL,
    queries      JSONB NOT NULL DEFAULT '[]',
    mode         TEXT NOT NULL,
    model        TEXT,
    problems     JSONB NOT NULL DEFAULT '[]',
    created_by   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalized_by TEXT,
    finalized_at TIMESTAMPTZ,
    history      JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS coding_patient ON coding_worksheet (patient_id);
