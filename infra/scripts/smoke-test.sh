#!/usr/bin/env bash
# Post-deploy checks: API health, status page, TLS on 443 and the PostgreSQL gateway.
set -euo pipefail
ENV_FILE="${DMX_ENV_FILE:-/etc/databasemax/.env}"
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
BASE="https://${DMX_DOMAIN}"
fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
curl -fsS --max-time 10 "$BASE/api/v1/health" | grep -q '"ok"' || fail "API health"
curl -fsS --max-time 10 "$BASE/api/v1/status" | jq -e '.components | length > 0' >/dev/null || fail "status"
curl -fsS --max-time 10 -o /dev/null "$BASE/" || fail "web"
# Gateway must refuse plaintext and speak TLS after SSLRequest.
out=$(PGCONNECT_TIMEOUT=5 psql "host=${DMX_DB_PUBLIC_HOST} port=${DMX_DB_PUBLIC_PORT:-5432} user=u_smoketest dbname=x sslmode=disable" -c 'select 1' 2>&1 || true)
echo "$out" | grep -q "TLS is required" || fail "gateway plaintext rejection: $out"
out=$(PGCONNECT_TIMEOUT=5 psql "host=${DMX_DB_PUBLIC_HOST} port=${DMX_DB_PUBLIC_PORT:-5432} user=u_smoketest dbname=x sslmode=verify-full sslrootcert=system" -c 'select 1' 2>&1 || true)
echo "$out" | grep -q "unknown endpoint" || fail "gateway TLS/verify-full: $out"
echo "smoke test passed"
