# Phases 3–4 report: cloning, backups, restores and production hardening

Date: 2026-09-28 · Commits after `29ad755` (see `git log`).

## Acceptance criteria

### Phase 3: branching, cloning and staging

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | A user can clone an eligible database | ✅ | `POST /databases/{id}/clone`, either from a backup or from the current data. Tested in `test_backups_integration.py::test_backup_clone_verify_and_final_backup` (API) and `e2e/backups.spec.ts` (UI). |
| 2 | A clone receives independent credentials | ✅ | New role, password and host. The test asserts the source's credentials are rejected by the clone and that the clone's URL differs. |
| 3 | Source and destination are isolated | ✅ | Separate instance, volumes and role. The test writes to the clone and checks the source is unchanged. |
| 4 | Failed clones are cleaned up safely | ✅ | Final failure removes the instance, volumes and unactivated credentials, and resets the restore marker. Tested by `test_corrupted_backup_fails_clone_and_cleans_up` and `test_failed_clone_resets_restore_marker`. |
| 5 | Resource quotas account for clone storage | ✅ | Clones go through the same transactional plan check as creates (count, storage, profile), and copies of current data also check the backup allowance. Tested by `test_clone_respects_plan_quota_and_validation` and `test_user_backup_allowance_and_delete`. |
| 6 | Restore procedures are tested | ✅ | Provisioner tests (data equality across tables, views, functions, sequences, partitions, ownership), API integration tests and the E2E test (restore from a deleted database's final backup, then `psql`). |

The Phase 3 items are implemented as follows:
- **Staging environments:** a copy can carry a different environment label.
- **Snapshots:** on-demand backups serve as development snapshots.
- **Clone status and history:** clones show provisioning stages, lineage on the overview tab and events on both the source and the clone.
- **Estimates:** the restore dialog shows the storage the copy will use against what remains on the plan.
- **Safer destructive operations:** delete dialogs offer a final backup, backups can be deleted with confirmation and recent re-authentication, and a suspended database can't be copied.
- **Not offered:** instant branching or copy-on-write. Cloning is backup-and-restore, and the product says so.

### Phase 4: production reliability and security hardening

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Backups run automatically | ✅ | The worker scheduler queues daily backups. Tested by `test_scheduler_queues_daily_backups_once`, and observed on the dev stack (`backup` jobs in the worker log). |
| 2 | Backup integrity checks work | ✅ | Per-chunk AEAD with counter, final flag and identity binding, plus a SHA-256 check before every restore. Tamper, truncation, reorder, splice, wrong key and wrong identity are rejected (`test_backup_crypto.py`). A corrupted object fails its clone cleanly. |
| 3 | A restore has been tested end to end | ✅ | See Phase 3 #6. Scheduled verification restores backups into scratch instances (`verify_backup`). |
| 4 | Monitoring detects defined critical failures | ✅ | Admin alerts cover: provisioner, worker or storage agent down; data-plane disk ≥ 80/90 %; backup coverage < 100 % in 26 h; failed backups; failed restore checks; queue age; missing instances; security thresholds (Admin → Security). Tested by `test_security_monitoring_flags_failed_logins`. |
| 5 | Resource quotas are enforced | ✅ | Hard per-database storage via fixed-size filesystems. `test_hard_quota_integration.py` fills a real database past its allocation: `DiskFull`, the server stays up, and growing the allocation restores writes. Plan quotas, per-credential API budgets and gateway per-client caps are also enforced. |
| 6 | Privileged endpoints have additional controls | ✅ | Admin role granted only via the audited CLI. Recent re-authentication and a reason are required for admin actions, and all of them are audited. The provisioner and storage agent expose closed operation sets on private network or socket. |
| 7 | Recovery procedures documented and tested | ✅ | Runbook sections cover backups, restores, key rotation, the storage agent, full volumes and control-DB restore. Customer restore paths are covered by automated tests; the control-DB restore script is documented but has not been drill-tested in this environment (see limitations). |
| 8 | Security review with tracked remediation list | ✅ | `docs/security/review-2026-09.md`: 2 high, 3 medium, 5 low and 5 info findings, all fixed or explicitly accepted, with regression tests. An external penetration test is still open. |

## Implemented functionality

- **Backups:**
  - pg_dump runs inside each instance and is streamed through AES-256-GCM, format DMXB2, with keys held only by the provisioner.
  - Stores are local (tested) or S3-compatible (tested against an emulator).
  - Kinds are daily, on demand, final (before delete) and pre-copy.
  - Retention follows the plan (3/7/14 days), with automatic expiry, purge of failed objects and a per-account allowance for user-triggered backups.
  - Users can delete their backups.
- **Restores and clones:** restores always go to a new database, run as a throwaway non-superuser role, from a single verified read of the object, with timeouts.
- **Verification:** scheduled restore tests run in scratch instances that have no customer network path, and compare against a snapshot-consistent table count.
- **Hard storage limits:**
  - The root storage agent keeps three fixed-size filesystems per instance (tablespace, WAL, PGDATA).
  - Volumes grow online, with an offline fallback.
  - Filesystems are remounted and fsck'd at boot, orphaned volumes are cleaned up, and capacity is counted from real image sizes.
- **Connection management:**
  - Gateway: per-client (IPv4 address or IPv6 /64) rate and concurrency caps, safe eviction, TCP keepalive.
  - PostgreSQL: idle-in-transaction timeout and keepalives.
- **API:** per-API-key, per-user and write budgets, plus limits on password reveals and API-key creation.
- **Operations:**
  - Admin → Backups: coverage, verification status, verify now.
  - Admin → Security: sign-in signals, sensitive actions, alerts.
  - Admin → Infrastructure: storage agent status and data-plane disk.
  - The worker's latency-sensitive loop (emails, heartbeat) is split from slow maintenance.
- **UI:**
  - A Backups tab on each database and an account-wide Backups page.
  - The restore/copy dialog shows an estimate.
  - Delete-with-final-backup, and backup deletion.
  - The dashboard shows a backups strip, and the overview shows lineage.
  - Docs page "Backups & restore"; marketing, pricing, security and terms copy updated. No PITR is claimed.

## Architecture changes

- Provisioner:
  - new modules `backup_crypto`, `backup_store`, `backups` and `storage_agent`, plus the `dmx_storage_agent` package;
  - `docker_backend` gains exec streaming, a loop storage backend, per-instance backend detection, the tablespace and WAL layout, resize and orphan cleanup;
  - new endpoints: backups, restore, verify, backup delete, storage resize, maintenance.
- Customer image entrypoint: optional `DMX_WALDIR` and `DMX_TBLSPC`, and a restore-role `pg_hba` rule.
- Control plane:
  - migrations `0003` (backup columns, job kinds, clone lineage, plan entitlements) and `0004` (backup allowance);
  - `backup_service`, `backups` router, and job handlers `backup`, `clone` and `verify_backup`;
  - long job leases, scheduler tasks, `monitoring/security`.
- Deployment:
  - `dmx-storage-agent.service` (installed by `host-setup.sh`);
  - the provisioner mounts `/run/databasemax` and the customer backup directory;
  - new environment variables in `.env.example`.
- Design revisions (documented in `docs/architecture/*`):
  - dumps run inside the instance rather than over the management network;
  - loop-mounted filesystems replace XFS project quotas;
  - the tablespace split was added after a checkpointer PANIC was observed during full-disk testing.

## Files (main additions)

`apps/provisioner/dmx_provisioner/{backup_crypto,backup_store,backups,storage_agent}.py`,
`apps/provisioner/dmx_storage_agent/agent.py`, `apps/provisioner/tests/test_{backup_crypto,backup_store,backups_integration,restore_privilege,storage_agent,hard_quota_integration}.py`,
`apps/api/app/{api/v1/backups.py,services/backup_service.py,monitoring/security.py}`,
`apps/api/migrations/versions/000{3,4}_*.py`, `apps/api/tests/test_{backups,backups_integration,storage,rate_limits}.py`,
`apps/web/features/backups/*`, `apps/web/app/(app)/backups`, `apps/web/app/admin/{backups,security}`,
`apps/web/app/docs/backups`, `apps/web/e2e/{backups.spec.ts,warmup.ts}`, `infra/storage-agent/*`,
`docs/security/review-2026-09.md`, `docs/architecture/backups.md` (rewritten).

## Commands

```sh
infra/scripts/dev-host.sh start               # now also starts the storage agent when DMX_STORAGE_BACKEND=loop
(cd apps/api && uv run pytest)                # 98
(cd apps/provisioner && sudo -E uv run pytest) # 58 (agent + hard-limit tests need root/loop devices)
(cd apps/gateway && uv run pytest)            # 13
pnpm --filter @databasemax/web test && pnpm --filter @databasemax/web test:e2e
```

## Tests and results (final runs in this environment)

| Suite | Result |
|---|---|
| API pytest (includes Docker integration tests against the loop-backed data plane) | **98 passed** |
| Provisioner pytest: crypto, stores (local and S3 emulator), backup/restore/verify on real instances, restore privilege regression, storage agent, hard-limit fill test | **58 passed** |
| Gateway pytest | **13 passed** |
| Web unit (Vitest) | **18 passed** |
| Playwright E2E (includes backup → delete with final backup → restore → `psql` data check) | **5 passed** |
| `tsc`, ESLint, `next build`, ruff, mypy (all services) | clean |
| pip-audit, `pnpm audit --prod` | no known vulnerabilities |
| Bandit (medium+) | 0 open |

**Flakiness found and fixed:**
- A 1-byte crypto assertion could fail by chance.
- Cold `next dev` compiles and per-IP sign-up limits broke local E2E runs; fixed with a warm-up global setup and a dev-only rate-limit reset.
- E2E runs left databases behind, and they filled the dev data-plane budget (the capacity check correctly refused new ones); E2E specs now clean up after themselves.

## Security checks performed

- An independent review pass with a tracked remediation list: `docs/security/review-2026-09.md`.
  - The highest finding (restore-time privilege escalation) was **reproduced**, fixed and regression-tested.
- Verified by tests:
  - backups contain no plaintext;
  - a tampered backup is rejected before restore;
  - the clone rejects the source's credentials;
  - a customer can't create tables in `pg_default`;
  - `COPY … TO PROGRAM` is denied after a malicious restore;
  - suspended databases can't be copied;
  - rate limits apply per credential;
  - attempted passwords never appear in admin security data.

## Known limitations

1. **The S3 store** has only been tested against an emulator. It must be exercised against OCI Object Storage before launch (checklist in `docs/deployment/oci.md` §10). With `local`, backups share the VM's failure domain.
2. **No point-in-time recovery**: RPO is up to 24 h, or the latest on-demand backup.
3. **Online volume grow** needs `CAP_SYS_RESOURCE`. This sandbox lacks it, so tests exercised the offline path (a brief stop and start); production hosts running the systemd service have it.
4. **Shrinking an allocation** keeps the larger filesystem; the soft read-only limit applies to the lower allocation (review item I3, accepted).
5. **Control-DB restore script**: documented, but no restore drill has been run in this environment.
6. **External penetration test**, seccomp/AppArmor tuning, internal TLS gateway → instance, and admin 2FA remain open (review "Open items").
7. **Worker metrics collection** is sequential. On a host with hundreds of instances, a cycle takes minutes. Acceptable for one VM; parallelise in Phase 5.

## Next phase checklist (Phase 5: multi-node)

- [ ] Host registry with per-host provisioner endpoint, credentials, capacity and health (heartbeats, failure detection)
- [ ] Capacity- and constraint-aware placement at create and clone time; the database records its host
- [ ] Control plane routes every infrastructure call to the database's host; the gateway resolves backends per host
- [ ] Host failure is visible to operators; databases on a failed host are flagged, **never silently reassigned**
- [ ] Draining a host (no new placements) and explicit, operator-initiated migration via backup/restore
- [ ] Per-host metrics aggregation; parallel collection
- [ ] Tests with two provisioners (two hosts simulated on one machine)
