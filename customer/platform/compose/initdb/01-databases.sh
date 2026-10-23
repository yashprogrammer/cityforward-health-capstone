#!/bin/sh
# Hospital IT's one-time database setup for the local stack (the Azure equivalent is Terraform + customer/meditrack/backend/bootstrap.py).
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE meditrack LOGIN PASSWORD 'meditrack';
CREATE DATABASE meditrack OWNER meditrack;
SQL
