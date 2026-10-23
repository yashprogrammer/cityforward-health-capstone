-- Append-only, hash-chained audit log. IDs and outcomes only: never clinical text, never prompts.
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    actor       TEXT NOT NULL,
    role        TEXT,
    action      TEXT NOT NULL,            -- ask | summary | index | priorauth.approve | ai.call | access.denied | break-glass | ...
    patient_id  TEXT,
    outcome     TEXT NOT NULL,            -- allowed | denied | error
    detail      JSONB NOT NULL DEFAULT '{}',
    request_id  TEXT,
    client_ip   TEXT,
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_patient ON audit_log (patient_id, ts);
CREATE INDEX IF NOT EXISTS audit_actor ON audit_log (actor, ts);

CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (% not allowed)', TG_OP USING ERRCODE = 'insufficient_privilege';
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_no_update ON audit_log;
CREATE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
DROP TRIGGER IF EXISTS audit_no_truncate ON audit_log;
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable();
