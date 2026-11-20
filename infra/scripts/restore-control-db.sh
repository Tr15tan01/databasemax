#!/usr/bin/env bash
# Restore the control-plane database from an encrypted backup. DESTRUCTIVE: stops the API/worker.
#   restore-control-db.sh <file.dump.age> <age-identity-file>
set -euo pipefail
FILE="${1:?backup file}"; IDENTITY="${2:?age identity (private key) file}"
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
sha256sum -c "$FILE.sha256"
read -r -p "This replaces the control database. Type RESTORE to continue: " ok; [ "$ok" = RESTORE ] || exit 1
systemctl stop dmx-api dmx-internal-api dmx-worker
age -d -i "$IDENTITY" "$FILE" | runuser -u postgres -- pg_restore -d dmx_control --clean --if-exists --no-owner --role=dmx_owner
systemctl start dmx-internal-api dmx-api dmx-worker
echo "restored; the worker's reconciler will re-sync provisioning state within minutes"
