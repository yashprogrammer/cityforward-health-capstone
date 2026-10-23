-- Row-level security: the database itself refuses to show a clinician patients outside their unit, even if application code forgets to check.
-- Settings are set per transaction by db.session(principal): app.user, app.role, app.units (comma list, 'ALL' for cross-unit roles), app.patients (SMART launch scope).
-- FORCE makes the policies apply to the table owner too. Superusers bypass RLS: the application must never connect as one.

CREATE TABLE IF NOT EXISTS break_glass (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    patient_id  TEXT NOT NULL,
    reason      TEXT NOT NULL,
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT
);
CREATE INDEX IF NOT EXISTS break_glass_lookup ON break_glass (user_id, patient_id, expires_at);

CREATE OR REPLACE FUNCTION app_can_see(unit_code text, pid text) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT CASE
        WHEN coalesce(current_setting('app.role', true), '') = '' THEN false
        WHEN current_setting('app.role', true) = 'SYSTEM' THEN true
        WHEN current_setting('app.role', true) NOT IN ('ATTENDING','RESIDENT','NURSE','PA_COORD','HIM_CODER','SERVICE') THEN false   -- IT admins and compliance read the audit log, not charts
        WHEN coalesce(current_setting('app.patients', true), '') <> ''
             AND NOT (pid = ANY (string_to_array(current_setting('app.patients', true), ','))) THEN false                              -- SMART launch is limited to one patient
        WHEN 'ALL' = ANY (string_to_array(coalesce(current_setting('app.units', true), ''), ',')) THEN true
        WHEN unit_code IS NULL THEN true
        WHEN unit_code = ANY (string_to_array(coalesce(current_setting('app.units', true), ''), ',')) THEN true
        WHEN EXISTS (SELECT 1 FROM break_glass b WHERE b.user_id = current_setting('app.user', true) AND b.patient_id = pid AND b.expires_at > now()) THEN true
        ELSE false END
$$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['chunk', 'patient_index', 'prior_auth', 'coding_worksheet'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS unit_scope ON %I', t);
        EXECUTE format('CREATE POLICY unit_scope ON %I USING (app_can_see(unit, patient_id)) WITH CHECK (app_can_see(unit, patient_id))', t);
    END LOOP;
END $$;
