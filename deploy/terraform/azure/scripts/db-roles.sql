-- Run once as the server admin from a host inside the VNet. Passwords come from Key Vault (the same values Terraform stored in the connection-URL secrets).
-- \set meditrack_pw '...'   \set copilot_pw '...'
CREATE ROLE meditrack LOGIN PASSWORD :'meditrack_pw';
CREATE ROLE copilot   LOGIN PASSWORD :'copilot_pw';
GRANT meditrack TO cfadmin;
GRANT copilot TO cfadmin;
ALTER DATABASE meditrack OWNER TO meditrack;
ALTER DATABASE copilot OWNER TO copilot;
\connect copilot
CREATE EXTENSION IF NOT EXISTS vector;      -- allow-listed by azure.extensions=VECTOR in main.tf; the application role is NOT a superuser, so row-level security applies to it
