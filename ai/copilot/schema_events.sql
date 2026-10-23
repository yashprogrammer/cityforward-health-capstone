-- Inbox for events pushed by the EHR. IDs only. The unique event id is the idempotency key (the EHR delivers at least once).
CREATE TABLE IF NOT EXISTS event_inbox (
    id            BIGSERIAL PRIMARY KEY,
    event_id      TEXT NOT NULL UNIQUE,
    type          TEXT NOT NULL,
    patient_id    TEXT,
    encounter_id  TEXT,
    unit          TEXT,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    status        TEXT NOT NULL DEFAULT 'RECEIVED',     -- RECEIVED | DONE | IGNORED | RETRY | DEAD
    attempts      INT NOT NULL DEFAULT 0,
    next_try      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error    TEXT,
    processed_at  TIMESTAMPTZ,
    result        JSONB
);
CREATE INDEX IF NOT EXISTS event_inbox_due ON event_inbox (status, next_try);
