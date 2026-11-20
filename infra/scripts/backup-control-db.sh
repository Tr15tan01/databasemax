#!/usr/bin/env bash
# Encrypted logical backup of the control-plane database (not customer databases).
#   BACKUP_AGE_RECIPIENT=age1...  BACKUP_BUCKET=databasemax-control-backups  backup-control-db.sh
set -euo pipefail
ENV_FILE="${DMX_ENV_FILE:-/etc/databasemax/.env}"
OUT_DIR="${BACKUP_DIR:-/var/lib/databasemax/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$OUT_DIR/control-$STAMP.dump.age"
# Settings may come from the environment or from the env file (plain KEY=value lines).
for var in BACKUP_AGE_RECIPIENT BACKUP_BUCKET; do
  if [ -z "${!var:-}" ] && [ -r "$ENV_FILE" ]; then
    val="$(grep -E "^${var}=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d "\"'")"
    [ -n "$val" ] && export "$var=$val"
  fi
done
: "${BACKUP_AGE_RECIPIENT:?set BACKUP_AGE_RECIPIENT in the env file (age public key from age-keygen; keep the private key offline)}"
install -d -m 0700 "$OUT_DIR"
runuser -u postgres -- pg_dump -d dmx_control --format=custom --no-owner \
  | age -r "$BACKUP_AGE_RECIPIENT" > "$FILE"
sha256sum "$FILE" > "$FILE.sha256"
if [ -n "${BACKUP_BUCKET:-}" ]; then
  oci os object put --bucket-name "$BACKUP_BUCKET" --file "$FILE" --name "control/$(basename "$FILE")" --auth instance_principal
  oci os object put --bucket-name "$BACKUP_BUCKET" --file "$FILE.sha256" --name "control/$(basename "$FILE").sha256" --auth instance_principal
fi
find "$OUT_DIR" -name 'control-*.dump.age*' -mtime +14 -delete
echo "$FILE"
