#!/usr/bin/env bash
# Build, migrate and roll out a release on a host prepared by host-setup.sh (systemd, no Docker).
#
#   sudo infra/scripts/deploy.sh <git-tag> [--skip-migrations]
#   sudo infra/scripts/deploy.sh local                  # deploy the files as they are (no git checkout)
#   sudo infra/scripts/deploy.sh local --role worker    # extra data-plane host: agent + provisioner only
#
# Customer databases (dmx-pg@*.service) keep running throughout. The gateway is only restarted
# when its code or configuration changed, because a restart drops open database connections.
set -euo pipefail
TAG="${1:?usage: deploy.sh <tag>|local [--skip-migrations] [--role worker]}"
shift
SKIP_MIGRATIONS=0 ROLE=all
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-migrations) SKIP_MIGRATIONS=1; shift ;;
    --role) ROLE="$2"; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${DMX_ENV_FILE:-/etc/databasemax/.env}"
STATE=/var/lib/databasemax/deploy
cd "$ROOT"
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE is missing: run infra/scripts/init-env.sh first" >&2; exit 1; }
install -d -m 0700 "$STATE"
val() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n1 | sed -e "s/^'\(.*\)'$/\1/" -e 's/^"\(.*\)"$/\1/'; }
DMX_ENV="$(val DMX_ENV)"

if [ "$TAG" != local ]; then
  echo "==> checking out $TAG"
  git fetch --tags --quiet && git checkout --quiet "$TAG"
else
  echo "==> deploying the working tree as-is"
fi
chmod -R go-w "$ROOT" && chmod -R a+rX "$ROOT"

echo "==> configuration"
python3 infra/scripts/render-env.py "$ENV_FILE" /etc/databasemax/env --role "$ROLE"
install -m 0644 infra/systemd/*.service infra/systemd/*.target /etc/systemd/system/
install -m 0755 infra/scripts/dmx-admin /usr/local/bin/dmx-admin
systemctl daemon-reload

echo "==> Python environments"
export UV_PYTHON_INSTALL_DIR=/opt/databasemax-python UV_PYTHON=3.13 UV_PYTHON_DOWNLOADS=never
export UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_CACHE_DIR=/var/cache/databasemax/uv
APPS="provisioner"
[ "$ROLE" = all ] && APPS="api gateway provisioner"
for app in $APPS; do
  (cd "apps/$app" && /usr/local/bin/uv sync --frozen --no-dev --quiet)
done

if [ "$ROLE" = all ]; then
  echo "==> web app"
  export NEXT_TELEMETRY_DISABLED=1 DMX_API_ORIGIN=http://127.0.0.1:8000 COREPACK_ENABLE_DOWNLOAD_PROMPT=0
  corepack enable >/dev/null 2>&1 || true
  corepack pnpm install --frozen-lockfile --filter @databasemax/web...
  corepack pnpm --filter @databasemax/web build
  SA=apps/web/.next/standalone/apps/web
  rm -rf "$SA/.next/static" "$SA/public"
  cp -r apps/web/.next/static "$SA/.next/static"
  cp -r apps/web/public "$SA/public"
  # Next's runtime cache must be writable; the code tree is read-only for the service.
  rm -rf "$SA/.next/cache" && ln -s /var/cache/databasemax/web "$SA/.next/cache"
  chmod -R a+rX apps/web/.next

  echo "==> control database roles and Redis password (from $ENV_FILE)"
  # Passed through the environment (runuser keeps it), never on a command line.
  DMX_OWNER_PASSWORD="$(val DMX_OWNER_PASSWORD)" DMX_APP_PASSWORD="$(val DMX_APP_PASSWORD)"
  export DMX_OWNER_PASSWORD DMX_APP_PASSWORD
  runuser -u postgres -- sh "$ROOT/infra/control-db/ensure-roles.sh"
  unset DMX_OWNER_PASSWORD DMX_APP_PASSWORD
  AUTH="requirepass $(val REDIS_PASSWORD)"
  if [ "$(cat /etc/redis/databasemax-auth.conf 2>/dev/null)" != "$AUTH" ]; then
    printf '%s\n' "$AUTH" > /etc/redis/databasemax-auth.conf
    chown root:redis /etc/redis/databasemax-auth.conf && chmod 0640 /etc/redis/databasemax-auth.conf
    systemctl restart redis-server
  fi

  if [ "$DMX_ENV" != production ] && [ ! -s /etc/databasemax/gateway-certs/fullchain.pem ]; then
    echo "==> development gateway certificate (self-signed CA, localhost and *.db.localhost)"
    TMP="$(mktemp -d)"
    bash infra/scripts/dev-certs.sh "$TMP" >/dev/null
    install -m 0640 -o root -g dmx-gw "$TMP/gateway.crt" /etc/databasemax/gateway-certs/fullchain.pem
    install -m 0640 -o root -g dmx-gw "$TMP/gateway.key" /etc/databasemax/gateway-certs/privkey.pem
    install -m 0644 "$TMP/ca.crt" /usr/local/share/databasemax-dev-ca.crt
    rm -rf "$TMP"
  fi
  [ -s /etc/databasemax/gateway-certs/fullchain.pem ] || {
    echo "missing /etc/databasemax/gateway-certs/fullchain.pem — see docs/deployment/oci.md §3" >&2; exit 1; }

  if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='dmx_control'" | grep -q 1 \
     && [ -n "$(val BACKUP_AGE_RECIPIENT)" ]; then
    echo "==> backing up the control database"
    "$ROOT/infra/scripts/backup-control-db.sh"
  elif [ "$DMX_ENV" = production ]; then
    echo "warning: BACKUP_AGE_RECIPIENT is not set — no control-database backup before this deploy" >&2
  fi

  if [ "$SKIP_MIGRATIONS" = 0 ]; then
    echo "==> migrations"
    systemctl start dmx-migrate.service || { journalctl -u dmx-migrate -n 40 --no-pager; exit 1; }
  fi

  echo "==> reverse proxy"
  if [ "$DMX_ENV" = production ]; then CF=infra/proxy/Caddyfile; else CF=infra/proxy/Caddyfile.local; fi
  install -m 0644 "$CF" /etc/caddy/Caddyfile
  systemctl reload-or-restart caddy
fi

echo "==> rolling out"
changed() {  # changed <name> <paths…>: true if the content hash differs from the last deploy
  local name=$1; shift
  local h; h="$(find "$@" -type f -not -path '*/__pycache__/*' -not -path '*/.venv/*' -print0 2>/dev/null \
    | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)"
  if [ "$(cat "$STATE/$name.sha" 2>/dev/null)" = "$h" ]; then return 1; fi
  echo "$h" > "$STATE/$name.sha"
}
restart() { systemctl enable --quiet "$1"; systemctl restart "$1"; }
if changed agent apps/provisioner/dmx_host_agent infra/systemd/dmx-host-agent.service /etc/databasemax/env/agent.env; then
  restart dmx-host-agent.service  # customer databases are separate units and keep running
fi
restart dmx-provisioner.service
if [ "$ROLE" = all ]; then
  for svc in dmx-internal-api dmx-api dmx-worker dmx-web; do restart "$svc.service"; sleep 2; done
  if changed gateway apps/gateway/dmx_gateway apps/gateway/uv.lock infra/systemd/dmx-gateway.service \
       /etc/databasemax/env/gateway.env || ! systemctl is-active --quiet dmx-gateway; then
    restart dmx-gateway.service
  else
    echo "gateway unchanged: not restarted (open database connections are kept)"
  fi
  systemctl enable --quiet databasemax.target
fi

echo "==> checks"
sleep 3
if [ "$ROLE" = all ]; then
  for i in $(seq 30); do curl -fsS --max-time 3 http://127.0.0.1:8000/api/v1/health >/dev/null && break; sleep 1; done
  curl -fsS --max-time 3 http://127.0.0.1:8000/api/v1/health >/dev/null || { journalctl -u dmx-api -n 40 --no-pager; exit 1; }
  if [ "$DMX_ENV" = production ]; then "$ROOT/infra/scripts/smoke-test.sh"; fi
fi
PROV="$(val DMX_PROVISIONER_LISTEN)"
for i in $(seq 20); do
  curl -fsS --max-time 3 "http://${PROV:-127.0.0.1}:8090/v1/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS --max-time 3 "http://${PROV:-127.0.0.1}:8090/v1/health" >/dev/null \
  || { journalctl -u dmx-provisioner -u dmx-host-agent -n 40 --no-pager; exit 1; }
systemctl --no-pager --plain list-units 'dmx-*' --type=service --all | sed -n '1,20p'
echo "deployed $TAG"
