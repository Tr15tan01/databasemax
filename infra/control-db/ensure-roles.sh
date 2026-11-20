#!/bin/sh
# Create (or update the passwords of) the control-plane database and its two roles. Idempotent.
#   dmx_owner - owns the schema, used only by migrations (MIGRATIONS_DATABASE_URL)
#   dmx_app   - runtime role for API and worker: DML only, audit table is insert/select only
#
# On a host: runuser -u postgres -- env DMX_OWNER_PASSWORD=… DMX_APP_PASSWORD=… sh ensure-roles.sh
# Elsewhere (CI, tests): set PGHOST/PGPORT/PGPASSWORD and POSTGRES_USER as usual for psql.
# Passwords are read from the environment inside psql (\getenv), never from the command line.
set -eu
: "${DMX_OWNER_PASSWORD:?DMX_OWNER_PASSWORD is required}"
: "${DMX_APP_PASSWORD:?DMX_APP_PASSWORD is required}"
psql -X -q -v ON_ERROR_STOP=1 ${POSTGRES_USER:+--username "$POSTGRES_USER"} --dbname postgres <<'SQL'
\getenv owner_pw DMX_OWNER_PASSWORD
\getenv app_pw DMX_APP_PASSWORD
SELECT 'CREATE ROLE dmx_owner LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dmx_owner') \gexec
SELECT 'CREATE ROLE dmx_app LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dmx_app') \gexec
ALTER ROLE dmx_owner WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'owner_pw';
ALTER ROLE dmx_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_pw';
SELECT 'CREATE DATABASE dmx_control OWNER dmx_owner' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'dmx_control') \gexec
REVOKE ALL ON DATABASE dmx_control FROM PUBLIC;
GRANT CONNECT ON DATABASE dmx_control TO dmx_app;
\connect dmx_control
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO dmx_owner;
GRANT USAGE ON SCHEMA public TO dmx_app;
ALTER DEFAULT PRIVILEGES FOR ROLE dmx_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dmx_app;
ALTER DEFAULT PRIVILEGES FOR ROLE dmx_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO dmx_app;
SQL
