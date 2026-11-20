#!/usr/bin/env bash
# Starts a throwaway PostgreSQL (port 55432) and Redis (port 56379) for the API test suite, separate
# from the installed control database. Run as root (uses the postgres OS user).
set -euo pipefail
PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
DATA="${DMX_DEV_PGDATA:-/var/lib/dmx-dev/pg}"
if [ ! -s "$DATA/PG_VERSION" ]; then
  install -d -o postgres -g postgres "$(dirname "$DATA")"
  # TCP logins need a password (tests check that wrong passwords are refused); the superuser
  # password is "postgres": DMX_TEST_PG_ADMIN=postgresql://postgres:postgres@127.0.0.1:55432/postgres
  runuser -u postgres -- "$PGBIN/initdb" -D "$DATA" -U postgres --auth-local=trust --auth-host=scram-sha-256 -E UTF8 >/dev/null
  NEW=1
fi
runuser -u postgres -- "$PGBIN/pg_ctl" -D "$DATA" -o "-p 55432 -k /tmp -c listen_addresses=127.0.0.1" -l "$DATA/../pg.log" status >/dev/null 2>&1 \
  || runuser -u postgres -- "$PGBIN/pg_ctl" -D "$DATA" -o "-p 55432 -k /tmp -c listen_addresses=127.0.0.1" -l "$DATA/../pg.log" -w start >/dev/null
if [ "${NEW:-0}" = 1 ]; then
  runuser -u postgres -- psql -X -q -h /tmp -p 55432 -U postgres -c "ALTER ROLE postgres PASSWORD 'postgres'"
  PGHOST=/tmp PGPORT=55432 POSTGRES_USER=postgres DMX_OWNER_PASSWORD=dmx_owner DMX_APP_PASSWORD=dmx_app \
    runuser -u postgres --preserve-environment -- sh "$(dirname "$0")/../control-db/ensure-roles.sh" >/dev/null
fi
redis-cli -p 56379 ping >/dev/null 2>&1 || redis-server --port 56379 --daemonize yes --save '' --appendonly no >/dev/null
echo "postgres :55432 and redis :56379 are up"
