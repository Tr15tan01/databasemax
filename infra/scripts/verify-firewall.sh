#!/usr/bin/env bash
# Run from OUTSIDE the VM/VCN. Confirms only the intended ports are reachable.
#   verify-firewall.sh <public-ip-or-host>
set -uo pipefail
HOST="${1:?host}"
ALLOWED="80 443 5432"
CHECK="21 23 25 2375 2376 3000 5433 6379 6432 8000 8001 8080 8090 9187 5432 443 80"
status=0
for p in $CHECK; do
  if timeout 3 bash -c "</dev/tcp/$HOST/$p" 2>/dev/null; then open=1; else open=0; fi
  if [[ " $ALLOWED " == *" $p "* ]]; then
    [ $open = 1 ] && echo "ok     $p open (expected)" || { echo "FAIL   $p closed (expected open)"; status=1; }
  else
    [ $open = 0 ] && echo "ok     $p closed" || { echo "FAIL   $p OPEN (must be closed)"; status=1; }
  fi
done
exit $status
