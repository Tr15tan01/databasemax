# Customer backups, restore verification and clones

Status: **implemented and tested** (see `docs/reports/phase-2-4.md` for test evidence and
limitations). No point-in-time recovery exists and none is advertised.

## What a backup is

A logical backup of one customer database: `pg_dump --format=custom --compress=6 --no-owner
--no-acl`. It contains schemas, tables and data, indexes, constraints, views, functions,
sequences and extensions. Roles and grants are intentionally excluded: a restore belongs to the
new database's own role.

| Kind | Trigger |
|---|---|
| `scheduled` | Worker scheduler, ~every 24 h per running database (first one ≥30 min after ready) |
| `manual` | Customer (`POST /databases/{id}/backups`), limited per plan per 24 h (1 / 5 / 20) |
| `final` | Optional step of `DELETE /databases/{id}?final_backup=true` (database must be running) |
| `pre_clone` | Taken automatically by "copy current data" clones |

Retention comes from the plan (`backup_retention_days`: Free 3, Developer 7, Pro 14) and is
fixed on each backup at completion (`expires_at`). The expiry task deletes the object and marks
the row `expired`.

- On-demand, pre-copy and final backups share a per-account allowance
  (`user_backup_storage_mb` = 2× the plan's total storage). Daily backups are bounded by
  retention and don't count toward it.
- Customers can delete any finished backup (`DELETE /backups/{id}`, recent re-auth, audited);
  the expiry task removes the object.
- Objects of failed backups are purged an hour after failure.
- Before each local dump the provisioner reserves the database's size against the disk budget,
  and requires free space of that estimate + 512 MB.

## Data path (control plane never sees contents)

```
worker ──HTTP (metadata only)──► provisioner ──pg_dump/pg_restore (fixed argv, loopback)──► customer instance
                                     │  pg_dump stdout ──► AES-256-GCM stream ──► BackupStore
                                     │  BackupStore ──► authenticate all chunks + checksum ──► decrypt ──► pg_restore stdin
```

- `pg_dump`/`pg_restore` run as children of the provisioner, using the binaries of the
  instance's own PostgreSQL version (`/usr/lib/postgresql/<version>/bin`), over loopback to the
  instance's port: dumps as the instance admin role (per-instance password, loopback-only in
  `pg_hba.conf`), restores as a throwaway non-superuser restore role. The children get a minimal
  environment (no provisioner configuration) and a `timeout` wrapper.
- The argv is a fixed list built by `dmx_provisioner.backups.dump_argv/restore_argv`; database and
  role names are regex-validated first and passed as separate argv entries (no shell).
- Restores use `--single-transaction --exit-on-error --role=<customer role> --no-owner --no-acl
  --no-tablespaces`, so a failed restore leaves the target empty and restored objects are owned by
  the new role.
- **Restores never run as a superuser.** Customer SQL executes during a restore (CHECK
  constraints, defaults, domain checks). `pg_restore` therefore logs in as a throwaway
  `dmx_r_<random>` role: `NOSUPERUSER`, member of the customer role only, `VALID UNTIL` +6 h,
  local-socket only (`local all +dmx_restorers scram-sha-256`, `host … reject`). The role is
  dropped afterwards. See security review finding H1.
- Dumps take a consistent table count from the same exported snapshot (`pg_dump --snapshot`),
  use `--lock-wait-timeout=60s`, and both dump and restore run under coreutils `timeout`
  (`DMX_BACKUP_TIMEOUT_SECONDS`). Backups never take the instance lock, so stop, delete and
  suspend always win.

## Encryption format ("DMXB2")

`apps/provisioner/dmx_provisioner/backup_crypto.py` — the chunked-AEAD ("STREAM") construction
built from AES-256-GCM (`cryptography`):

```
header  = "DMXB2" | u8 len(key_id) | key_id | 8-byte random nonce prefix
chunk_i = u32 len | AES-GCM(key, nonce = prefix || u32 i, plaintext_i (≤1 MiB),
                            aad = header || u32 i || u8 is_final || "<database id>/<backup id>")
```

- Header (and therefore the key id) is authenticated in every chunk, and so is the backup's
  identity (the reader supplies the expected ids). An object can't be swapped for another
  backup. `DMXB1` objects (without identity binding) remain readable.
- The counter prevents reordering/duplication/splicing between backups; the final flag prevents
  truncation; trailing bytes after the final chunk are rejected.
- Keys: `DMX_BACKUP_KEYS` (JSON `{key_id: base64 32 bytes}`) + `DMX_BACKUP_ACTIVE_KEY`, set **only
  on the provisioner**. The API, worker and web never hold them. Old keys stay listed after
  rotation so older backups remain restorable. Production refuses to start without a non-dev key.
- The control plane records `sha256` of the ciphertext, size, key id, table count, duration.

Before any byte reaches `pg_restore`, the provisioner makes a full pass over the object. Every
chunk is authenticated, and the SHA-256 must equal the value the control plane recorded. Only
then does a second streaming pass over the **same open file** (rewound) feed `pg_restore`.
Remote objects are first spooled, still encrypted, to a private temp file.

## Storage

`BackupStore` (`backup_store.py`), object key `<database uuid>/<backup uuid>.dmxb` derived only
from validated UUIDs:

- **local** — directory on the provisioner host (`/var/lib/databasemax/customer-backups`, mode
  0700, uid 10002). Atomic writes (temp file + fsync + rename), files 0600, disk budget
  (`DMX_BACKUP_DISK_BUDGET_MB`). Survives container/volume loss but **not loss of the VM**.
- **s3** — any S3-compatible endpoint; intended target: OCI Object Storage's S3 compatibility API
  (Customer Secret Key scoped to one bucket, bucket private with versioning + lifecycle rule).
  Ciphertext is spooled locally then uploaded with multipart. Tested against an S3 emulator
  (moto); **not yet exercised against OCI itself**.

## Restore = always a new database ("clone")

`POST /databases/{id}/clone {name, backup_id?, project_id?, environment?, compute_profile?}`

- Creates a new `databases` row (quota-checked like any create) with `source_database_id` and
  `restored_from_backup_id`, then a `clone` job: provision → configure role/db → **restore** →
  TLS connectivity test → ready. `data_restored_at` makes the restore step idempotent on retry.
- Without `backup_id`, the job first takes a `pre_clone` backup of the (running) source.
- Works for deleted databases while their backups are retained (final backups).
- Failure handling is the same as create: after the final attempt the instance, volume and
  unactivated credentials are removed. Integrity failures are non-retryable
  (`backup_integrity`).

## Verification

- Scheduler (hourly, `DMX_BACKUP_VERIFY_PER_HOUR`, default 3): picks recent unverified backups of
  databases without a verification in the last 7 days.
- `verify_backup` job → provisioner `POST /v1/backups/verify`: authenticate + checksum, create a
  scratch instance (`purpose=verify` label, **management network only**, no gateway network),
  restore, compare the table count with the count recorded at backup time, destroy the scratch
  instance (always, in `finally`; a sweep removes any left after 1 h). The reconciler ignores
  `purpose=verify` instances.
- Result: `verification_status = passed|failed`, `verified_at`; failures raise an admin alert and
  an audit event.

## Scheduling, leases and failure handling

- Backup/restore/verify calls can be long: the worker extends the job lease to
  `DMX_LONG_JOB_LEASE_SECONDS` (default 3900 s) before the call.
- Scheduled backup failures back off 6 h per database and notify the owner at most once per day;
  manual/final failures always notify. `backup` and `verify_backup` jobs do not count against a
  user's concurrent-operation limit.
- A delete with a final backup never destroys data without the backup: if the backup fails, the
  database stays `deleting` (connections refused) and the delete (including the backup) is retried
  every 10 minutes; operators are alerted via the failed job.
- Pending/running backup rows whose job disappeared (e.g. cancelled by a deletion) are closed as
  failed by the expiry task.

## Targets (single VM)

- RPO: ≤ 24 hours (daily logical backups). On-demand backups before risky changes.
- RTO: measured in tests at a few seconds for small databases (~2 k rows); larger databases scale
  with `pg_restore` throughput — measure with restore drills before quoting numbers.

## Not implemented / not claimed

Point-in-time recovery (WAL archiving), cross-region copies, customer-managed keys, restore in
place, per-table restore, backup download by customers.
