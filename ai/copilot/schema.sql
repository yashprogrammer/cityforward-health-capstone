-- Copilot database. Owned by the `copilot` role; the extension is created by the platform team (see platform/compose/initdb).
CREATE TABLE IF NOT EXISTS chunk (
    id           BIGSERIAL PRIMARY KEY,
    patient_id   TEXT NOT NULL,
    encounter_id TEXT,
    unit         TEXT,                       -- ward code; row-level security keys on this
    source_ref   TEXT NOT NULL,              -- the FHIR resource this text came from, e.g. DocumentReference/note-12
    kind         TEXT NOT NULL,              -- note | lab | vital | diagnosis | procedure | medication | encounter | document
    effective    TIMESTAMPTZ,
    text         TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding    vector(384) NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    indexed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (patient_id, source_ref, content_hash)
);
CREATE INDEX IF NOT EXISTS chunk_patient ON chunk (patient_id);
CREATE INDEX IF NOT EXISTS chunk_tsv ON chunk USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunk_embedding ON chunk USING hnsw (embedding vector_cosine_ops);

-- which patients are indexed, and how fresh
CREATE TABLE IF NOT EXISTS patient_index (
    patient_id  TEXT PRIMARY KEY,
    unit        TEXT,
    chunks      INT NOT NULL,
    indexed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- every model call, metered (tokens, latency, cost) and attributable. Prompts and replies are NOT stored: they contain PHI.
CREATE TABLE IF NOT EXISTS llm_call (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    purpose     TEXT NOT NULL,                 -- ask | summary | prior-auth | coding | ...
    patient_id  TEXT,
    actor       TEXT,
    model       TEXT NOT NULL,
    tokens_in   INT NOT NULL DEFAULT 0,
    tokens_out  INT NOT NULL DEFAULT 0,
    ms          INT NOT NULL DEFAULT 0,
    cost_usd    NUMERIC(10,6) NOT NULL DEFAULT 0,
    redactions  INT NOT NULL DEFAULT 0,        -- identifiers replaced by placeholders before the call
    status      TEXT NOT NULL DEFAULT 'ok'     -- ok | error | invalid
);
CREATE INDEX IF NOT EXISTS llm_call_ts ON llm_call (ts);
