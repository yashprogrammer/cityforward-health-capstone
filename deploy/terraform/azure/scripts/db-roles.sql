-- Run once as the server admin from a host inside the VNet. Passwords come from Key Vault (the same values Terraform stored in the connection-URL secrets).
-- \set meditrack_pw '...'   \set copilot_pw '...'
-- Idempotent: a rerun updates the passwords instead of failing.
SELECT format('CREATE ROLE %I LOGIN', r) FROM (VALUES ('meditrack'), ('copilot')) v(r) WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r) \gexec
ALTER ROLE meditrack PASSWORD :'meditrack_pw';
ALTER ROLE copilot PASSWORD :'copilot_pw';
GRANT meditrack TO cfadmin;
GRANT copilot TO cfadmin;
ALTER DATABASE meditrack OWNER TO meditrack;
ALTER DATABASE copilot OWNER TO copilot;
\connect copilot
CREATE EXTENSION IF NOT EXISTS vector;      -- allow-listed by azure.extensions=VECTOR in main.tf; the application role is NOT a superuser, so row-level security applies to it
CREATE EXTENSION IF NOT EXISTS pgaudit;     -- statement-level audit (pgaudit.log is set server-wide in main.tf); logs flow to Log Analytics via the PostgreSQL diagnostic setting
