#!/bin/sh
# Hospital IT's one-time database setup for the local stack (the Azure equivalent is Terraform + customer/meditrack/backend/bootstrap.py).
# The vendor's database is a separate database with its own role: the AI service never sees MediTrack's tables (it uses the FHIR API).
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE meditrack LOGIN PASSWORD 'meditrack';
CREATE DATABASE meditrack OWNER meditrack;
CREATE ROLE copilot LOGIN PASSWORD 'copilot';
CREATE DATABASE copilot OWNER copilot;
SQL
# pgvector needs a superuser once; after that the vendor's role owns everything else
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname copilot -c "CREATE EXTENSION IF NOT EXISTS vector"
