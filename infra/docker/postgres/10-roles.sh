#!/bin/sh
# Runs once on first container start (docker-entrypoint-initdb.d).
# Creates the two application roles and the app + test databases.
#   app_owner — owns the schema, runs Alembic migrations.
#   app_rw    — what the API and worker connect as. NOSUPERUSER + NOBYPASSRLS,
#               so Postgres row-level security always applies (ADR-0004).
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE app_owner LOGIN PASSWORD '${APP_OWNER_PASSWORD}' NOSUPERUSER NOBYPASSRLS NOCREATEROLE;
CREATE ROLE app_rw    LOGIN PASSWORD '${APP_RW_PASSWORD}'    NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
CREATE DATABASE momentsales      OWNER app_owner;
CREATE DATABASE momentsales_test OWNER app_owner;
SQL
for db in momentsales momentsales_test; do
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<SQL
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS vector;
ALTER SCHEMA public OWNER TO app_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app_rw;
SQL
done
