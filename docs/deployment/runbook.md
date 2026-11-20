# Operational runbook

All commands run on the VM as root (`sudo -i`). Everything is a systemd unit: the control plane
(`dmx-api`, `dmx-internal-api`, `dmx-worker`, `dmx-gateway`, `dmx-web`, `dmx-provisioner`,
`dmx-host-agent`, grouped by `databasemax.target`) and one `dmx-pg@<instance id>` per customer
database. The operator CLI is `dmx-admin <command>`. An instance id is shown on Admin →
Databases → database ("internal name").

## Daily checks
- Admin → **Infrastructure**: alerts list must be empty.
- Admin → **Backups**: coverage should be *N/N in 26 h*; no failed restore checks.
- Admin → **Provisioning queue**: nothing queued for more than 10 minutes.
- Disk: `df -h /var/lib/postgresql /var/lib/databasemax` below 80%.

## Provisioning is stuck
1. Admin → Infrastructure: is the worker heartbeat healthy? If not: `journalctl -u dmx-worker -n 200`, then `systemctl restart dmx-worker`.
   Jobs are leased in PostgreSQL; a restarted worker reclaims expired leases automatically.
2. Provisioner reachable? `journalctl -u dmx-provisioner -u dmx-host-agent -n 100`. Capacity exhausted shows as
   `capacity_exceeded` on jobs — add capacity or reduce the budget; jobs retry with backoff.
3. A single database failed: Admin → Databases → the database → **Retry provisioning** (reason required).

## Customer reports "cannot connect"
1. Ask for the exact error and time (and request ID for API errors).
2. `unknown endpoint or user`: host/username mismatch — have them copy the URL again.
3. `password authentication failed`: credentials were rotated or mistyped.
4. `database is stopped` / `not available`: check status in Admin → Databases.
5. Gateway health: `curl -s http://127.0.0.1:9187/` or Admin → Infrastructure; logs: `journalctl -u dmx-gateway`.
6. Instance health: `systemctl status dmx-pg@<instance>`; logs with `journalctl -u dmx-pg@<instance>` (no customer data is logged; statement logging is off).

## A customer instance crashed / won't start
- `systemctl status dmx-pg@<instance>` and `journalctl -u dmx-pg@<instance> -n 100`;
  `journalctl -k | grep -i oom` shows out-of-memory kills.
- Out of memory: the database exceeds its memory profile (`MemoryMax`). PostgreSQL recovers from
  a killed backend on its own; if it keeps happening, inform the customer. Inform the customer; a larger size requires
  A larger size requires a plan upgrade (compute resize is a Phase 2+ feature — currently
  recreate + restore via a backup).
- The unit restarts on failure (5 times in 5 minutes, then it stays failed): after fixing the
  cause, `systemctl reset-failed dmx-pg@<instance>` and start it from Admin → Databases.
- Never delete `/var/lib/databasemax/data-plane/images/<instance>*` or `mnt/<instance>*` while
  investigating.

## Host reboot
systemd starts the host agent (which re-mounts every database filesystem first), the customer
databases and the control plane. After boot: `systemctl --failed`, then Admin → Infrastructure;
the reconciler refreshes endpoints within 5 minutes. Stopped databases stay stopped.

## Suspend an abusive account
Admin → Users → user → **Suspend** (requires password confirmation and a reason). Sessions are
revoked immediately; API keys and database connections are refused while suspended.

## Rotate platform secrets
- `SESSION_SECRET`: rotating signs everyone out. Update `.env`, run `infra/scripts/deploy.sh local`.
- `DMX_SECRETS_KEYS`: add a new key id, set `DMX_SECRETS_ACTIVE_KEY` to it, deploy, then run
  `dmx-admin reencrypt-secrets`; remove the old key in the next deploy.
- `PROVISIONER_TOKEN` / `INTERNAL_API_TOKEN`: update `.env` and run `deploy.sh local` (it restarts everything that uses them).
- `PROVISIONER_ADMIN_SECRET`: per-instance admin passwords derive from it; instances pick up a new
  verifier when they are started by the platform, so rotate during a maintenance window and
  stop/start databases one by one from Admin → Databases.

## Control database restore
`infra/scripts/restore-control-db.sh <backup.dump.age> <identity>` — stops API/worker, restores,
restarts. Customer instances are unaffected; the reconciler re-syncs state.

## Hosts (multi-node)

- **Host offline** (critical alert): its databases are unreachable and are **not** moved.
  1. Check the VM in the OCI console, then on it `systemctl status dmx-host-agent dmx-provisioner`
     and `systemctl --failed`.
  2. Bring the provisioner back; the host returns to its previous status on the next heartbeat.
  3. If the VM is lost for good, restore the affected databases from their latest backups:
     use S3 backups, or local backups replicated elsewhere. With local-only backups on a lost
     VM there is nothing to restore from, so use the shared store for production.
- **Drain** (Admin → Hosts): stops new placements; existing databases keep running.
- **Move a database** (Admin → Databases → database → **Move host**): the database is read-only,
  then briefly unavailable, and keeps the same URL and password. Move them one at a time; watch
  Admin → Provisioning queue.
- **Retire** after the host is empty, then decommission the VM and remove its firewall rules.
- **Add a host:** `docs/deployment/oci.md` §11.

## Host agent / full volumes

- `systemctl status dmx-host-agent` and `journalctl -u dmx-host-agent`. If it is down,
  provisioning fails with `host_agent_unreachable` (jobs retry); running databases are not
  affected (they are separate units and their filesystems stay mounted).
- After a reboot the agent fsck's and re-mounts every image **before the databases start**; if an
  instance fails to start, check `findmnt /var/lib/databasemax/data-plane/mnt/<instance>-sys`.
- A customer at their hard limit sees `No space left on device` errors. They (or support, via
  a plan change) increase the allocation in Settings; the volume grows online.
- Host view: `ls -ls /var/lib/databasemax/data-plane/images` (first column = real blocks used;
  images are sparse).

## Backups

- **Failed backups** (Admin → Backups, filter *failed*): the error column says why. `instance
  missing` means the database unit is gone — see "A customer instance crashed". Disk budget errors
  (`capacity_exceeded`) mean `DMX_BACKUP_DISK_BUDGET_MB` is reached: expire/move old backups or
  raise the budget. Scheduled backups retry after 6 h; take one immediately from Admin →
  Databases → database → **Back up now**.
- **Failed restore check**: treat as an incident. Keep the backup (do not delete), run **Verify**
  again, then restore the backup into a new database as the customer would to see the error.
  Inform the customer if their latest backups are affected and suggest an on-demand backup.
- **Restore for a customer**: customers restore themselves (dashboard → Backups → Restore). Support
  never restores in place. If they can't (quota), temporarily raise their plan or delete a
  database on their instruction.
- **Rotate the backup key**: add a new key to `DMX_BACKUP_KEYS` (keep the old ones), set
  `DMX_BACKUP_ACTIVE_KEY` to it, redeploy the provisioner. Remove an old key only after every
  backup encrypted with it has expired (Admin → Backups shows the key id per backup).
- **Lost backup key** = those backups are unrecoverable. Keep an offline copy of `DMX_BACKUP_KEYS`.

## Escalation
Security incidents → `docs/security/incident-response.md`.
