#!/usr/bin/env bash
# Create /etc/databasemax/.env with freshly generated secrets and the local wiring of a systemd
# install. Never overwrites an existing file. Review it afterwards (SMTP, region, backups).
#
#   init-env.sh --domain example.com --email ops@example.com        # production
#   init-env.sh --dev                                               # local / test VM (http://localhost)
#   init-env.sh --worker --host-name worker-2 --private-ip 10.0.1.12 --gateway-ip 10.0.1.11
set -euo pipefail
ENV_FILE="${DMX_ENV_FILE:-/etc/databasemax/.env}"
DOMAIN="" EMAIL="" DEV=0 WORKER=0 HOST_NAME="$(hostname -s)" PRIVATE_IP="" GATEWAY_IP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --email) EMAIL="$2"; shift 2 ;;
    --dev) DEV=1; shift ;;
    --worker) WORKER=1; shift ;;
    --host-name) HOST_NAME="$2"; shift 2 ;;
    --private-ip) PRIVATE_IP="$2"; shift 2 ;;
    --gateway-ip) GATEWAY_IP="$2"; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
if [ -e "$ENV_FILE" ]; then echo "$ENV_FILE already exists; not touching it"; exit 0; fi
if [ "$DEV" = 0 ] && [ "$WORKER" = 0 ] && { [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; }; then
  echo "production needs --domain and --email (or use --dev)" >&2; exit 2
fi
if [ "$WORKER" = 1 ] && { [ -z "$PRIVATE_IP" ] || [ -z "$GATEWAY_IP" ]; }; then
  echo "a worker host needs --private-ip and --gateway-ip" >&2; exit 2
fi

hex() { openssl rand -hex 32; }        # passwords that end up inside URLs
key() { openssl rand -base64 32; }     # 32-byte encryption keys

# Data-plane budget from this machine: leave ~3 GB RAM and one CPU for the control plane and OS.
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
CPUS=$(nproc)
DATA_MEM=$(( MEM_MB > 4096 ? MEM_MB - 3072 : MEM_MB / 2 ))
DATA_CPUS=$(( CPUS > 1 ? CPUS - 1 : 1 ))
mkdir -p /var/lib/databasemax
FREE_MB=$(df -Pm /var/lib/databasemax | awk 'NR==2 {print $4}')
DATA_DISK=$(( FREE_MB * 6 / 10 ))
MAX_INSTANCES=$(( DATA_MEM / 256 ))
STORAGE=dir
if [ "$DEV" = 0 ] && modprobe -n loop >/dev/null 2>&1; then STORAGE=loop; fi

install -d -m 0700 "$(dirname "$ENV_FILE")"
umask 077
OWNER=$(hex); APP=$(hex); REDIS=$(hex)
if [ "$DEV" = 1 ]; then
  ENVIRONMENT=development WEB=http://localhost COOKIE=false EMAIL_BACKEND=file
  DB_HOST=localhost DB_SUFFIX=db.localhost DOMAIN=localhost EMAIL="ops@localhost"
else
  ENVIRONMENT=production WEB="https://$DOMAIN" COOKIE=true EMAIL_BACKEND=smtp
  DB_HOST="db.$DOMAIN" DB_SUFFIX=""
fi

if [ "$WORKER" = 1 ]; then
  cat > "$ENV_FILE" <<EOF
# DatabaseMax worker host ($HOST_NAME). Generated $(date -u +%FT%TZ). See docs/deployment/oci.md §11.
DMX_ENV=production
DMX_HOST_NAME=$HOST_NAME
DMX_REGION=oci-local-1
# A NEW token pair for this host — register it from the control plane with app.cli register-host.
PROVISIONER_TOKEN=$(hex)
PROVISIONER_ADMIN_SECRET=$(hex)
# The provisioner listens on the private address; firewall it to the control-plane VM.
DMX_PROVISIONER_LISTEN=$PRIVATE_IP
DMX_INSTANCE_LISTEN=$PRIVATE_IP
DMX_INSTANCE_CLIENT_CIDRS=$GATEWAY_IP/32
DMX_PG_VERSIONS=16
DMX_DATA_PLANE_MEMORY_MB=$DATA_MEM
DMX_DATA_PLANE_CPUS=$DATA_CPUS
DMX_DATA_PLANE_DISK_MB=$DATA_DISK
DMX_MAX_INSTANCES=$MAX_INSTANCES
DMX_STORAGE_BACKEND=$STORAGE
# Must be the SAME keys as on every other host (copy them from the control-plane VM).
DMX_BACKUP_KEYS='{"b1":"REPLACE_WITH_THE_CONTROL_PLANE_KEY"}'
DMX_BACKUP_ACTIVE_KEY=b1
DMX_BACKUP_STORE=local
EOF
  echo "wrote $ENV_FILE (worker). Copy DMX_BACKUP_KEYS from the control-plane VM before deploying."
  exit 0
fi

cat > "$ENV_FILE" <<EOF
# DatabaseMax configuration — the only file to edit; deploy.sh splits it per service.
# Generated $(date -u +%FT%TZ). Keep a copy of DMX_BACKUP_KEYS offline: without it no backup
# can be restored.
DMX_ENV=$ENVIRONMENT
DMX_DOMAIN=$DOMAIN
DMX_ACME_EMAIL=$EMAIL
DMX_PUBLIC_WEB_URL=$WEB
DMX_PUBLIC_API_URL=$WEB/api
DMX_CORS_ORIGINS=$WEB
DMX_COOKIE_SECURE=$COOKIE
DMX_REGION=oci-local-1
DMX_REGION_LABEL="Oracle Cloud"
DMX_LOG_LEVEL=INFO

# --- control database (local PostgreSQL, unix socket only) and Redis (loopback) -----------
DMX_OWNER_PASSWORD=$OWNER
DMX_APP_PASSWORD=$APP
DATABASE_URL=postgresql+psycopg://dmx_app:$APP@/dmx_control?host=/var/run/postgresql
MIGRATIONS_DATABASE_URL=postgresql+psycopg://dmx_owner:$OWNER@/dmx_control?host=/var/run/postgresql
REDIS_PASSWORD=$REDIS
REDIS_URL=redis://:$REDIS@127.0.0.1:6379/0

# --- secrets ------------------------------------------------------------------------------
SESSION_SECRET=$(hex)
DMX_API_KEY_PEPPER=$(hex)
DMX_SECRETS_KEYS='{"k1":"$(key)"}'
DMX_SECRETS_ACTIVE_KEY=k1
PROVISIONER_TOKEN=$(hex)
PROVISIONER_ADMIN_SECRET=$(hex)
INTERNAL_API_TOKEN=$(hex)

# --- customer backups (keys only on the provisioner) ---------------------------------------
DMX_BACKUP_KEYS='{"b1":"$(key)"}'
DMX_BACKUP_ACTIVE_KEY=b1
DMX_BACKUP_STORE=local
DMX_BACKUP_DISK_BUDGET_MB=$(( DATA_DISK / 3 ))

# --- email --------------------------------------------------------------------------------
DMX_EMAIL_BACKEND=$EMAIL_BACKEND
DMX_EMAIL_FROM="DatabaseMax <no-reply@$DOMAIN>"
SMTP_HOST=CHANGE_ME
SMTP_PORT=587
SMTP_USERNAME=CHANGE_ME
SMTP_PASSWORD=CHANGE_ME
SMTP_STARTTLS=true

# --- customer databases ---------------------------------------------------------------------
DMX_PG_VERSIONS=16
DMX_DEFAULT_PG_VERSION=16
DMX_DB_PUBLIC_HOST=$DB_HOST
DMX_DB_HOST_SUFFIX=$DB_SUFFIX
DMX_DB_PUBLIC_PORT=5432
DMX_HOST_NAME=$HOST_NAME
DMX_DATA_PLANE_MEMORY_MB=$DATA_MEM
DMX_DATA_PLANE_CPUS=$DATA_CPUS
DMX_DATA_PLANE_DISK_MB=$DATA_DISK
DMX_MAX_INSTANCES=$MAX_INSTANCES
# loop: hard per-database storage limits (needs loop devices); dir: soft limits
DMX_STORAGE_BACKEND=$STORAGE

# --- billing (optional) ---------------------------------------------------------------------
PADDLE_ENVIRONMENT=sandbox

# --- control-database backups: age public key (keep the private key off this machine) -------
BACKUP_AGE_RECIPIENT=
EOF
chmod 600 "$ENV_FILE"
echo "wrote $ENV_FILE"
[ "$DEV" = 1 ] || echo "next: set the SMTP_* values (and BACKUP_AGE_RECIPIENT), then run deploy.sh"
