# Deploying DatabaseMax on Oracle Cloud Infrastructure (single VM)

This guide deploys the whole platform — control plane and customer databases — on one Ubuntu
24.04 VM, with everything running as systemd services (no Docker). The same steps work on any
Ubuntu 24.04 VPS. **Nothing here makes the VM secure automatically; each step below matters.**

> ⚠️ **Before accepting production data:** configure customer backups to go **off the VM**
> (`DMX_BACKUP_STORE=s3` with OCI Object Storage, §10) and run a restore drill. Local backups
> protect against mistakes and instance loss, not against losing the VM.

## 0. Order of steps

1. Create the VM, block volumes, security rules and DNS records (§1–§2).
2. Put the code on the VM **at `/opt/databasemax`** (the units run it from there): either
   `git clone <your repo> /opt/databasemax`, or upload an archive and unpack it there.
3. `sudo /opt/databasemax/infra/scripts/host-setup.sh` (§4).
4. `sudo /opt/databasemax/infra/scripts/init-env.sh --domain <domain> --email <you>` and review
   `/etc/databasemax/.env` (§5); gateway certificate (§3); customer-backup bucket (§10).
5. `sudo infra/scripts/deploy.sh <tag>` from a git checkout, or `deploy.sh local` (§6).
6. Register your account in the web UI, grant yourself admin, run the §8 checklist.

## 1. Capacity planning

| Component | CPU (limit) | Memory (limit) |
|---|---|---|
| Caddy | — | ~100 MB |
| Web (Next.js) `dmx-web` | 0.75 | 768 MB |
| API (3 workers) `dmx-api` | 1.0 | 1 GB |
| Internal API `dmx-internal-api` | 0.5 | 384 MB |
| Worker `dmx-worker` | 0.75 | 512 MB |
| Provisioner `dmx-provisioner` + host agent | 0.5 | 512 + 512 MB |
| Gateway `dmx-gateway` | 1.0 | 512 MB |
| Control PostgreSQL | — | ~1 GB |
| Redis | — | 256 MB |
| **Control plane total** | **≈5 (burst)** | **≈5 GB** |
| OS and page cache headroom | — | ≥2 GB |

Limits are set in the unit files (`infra/systemd/`). All customer databases together run in
`dmx-customers.slice`, capped at the data-plane budget, so they can never starve the control
plane. Per database: nano 0.25 vCPU / 256 MB, small 0.5 / 512 MB, medium 1 / 1 GB.

**Recommended VM:** `VM.Standard.A1.Flex` with **4 OCPU / 24 GB** (Arm) or `VM.Standard.E5.Flex`
4 OCPU / 32 GB. `init-env.sh` sizes the data-plane budget from the VM (RAM minus ~3 GB, CPUs
minus one, 60% of free disk); adjust `DMX_DATA_PLANE_*` afterwards if needed.

**Always Free tier:** in mid-2026 Oracle documented a cut of the Always Free Ampere allowance from
4 OCPU / 24 GB to 2 OCPU / 12 GB (enforcement has been reported as inconsistent; check your
tenancy's limits). 2 OCPU / 12 GB runs the platform for a trial with a handful of nano databases,
not for paying customers.

**Disk:** attach two encrypted Block Volumes:
- `/var/lib/postgresql` — control database (50 GB, balanced performance)
- `/var/lib/databasemax` — customer databases (`data-plane/`) and local backups (size = sum of
  plan allocations × 1.3; start with 500 GB, higher performance tier)

Block Volumes are encrypted at rest by OCI by default; use a customer-managed key in OCI Vault
if your compliance needs it. Mount them (fstab) **before** running host-setup.sh.

## 2. Network and DNS

### OCI security list / NSG (ingress)

| Port | Source | Purpose |
|---|---|---|
| 22/tcp | your admin IPs only (or use OCI Bastion) | SSH |
| 80/tcp | 0.0.0.0/0 | ACME HTTP challenge, redirect to HTTPS |
| 443/tcp, 443/udp | 0.0.0.0/0 | Web app and API |
| 5432/tcp | 0.0.0.0/0 (or customer allowlist) | PostgreSQL gateway (TLS required) |

Egress: allow 443 (package updates, Paddle, Google, ACME, object storage), 587 (SMTP).

### What listens where

Only Caddy (80/443) and the gateway (5432) listen on public interfaces. Everything else binds to
loopback or a unix socket: API 127.0.0.1:8000, internal API :8001, web :3000, provisioner :8090,
gateway status :9187, Redis :6379, the control PostgreSQL (unix socket only), and each customer
database on 127.0.0.1:20000–29999. Customer database units also have `IPAddressDeny=any` (they
can't open outbound connections).

### Host firewall

OCI's Ubuntu images ship iptables rules ending in `REJECT` for INPUT. `host-setup.sh` inserts a
`DMX-INPUT` chain at the top of INPUT that accepts 80, 443 (tcp/udp) and 5432, and saves the rules
with netfilter-persistent. On a VPS without a default-reject policy, add one (keeping SSH open)
— the services above don't need any other inbound port.

Verify from **another machine** with `infra/scripts/verify-firewall.sh <public-ip>`; it checks that
only 22 (from your IP), 80, 443 and 5432 answer.

### DNS

| Record | Value |
|---|---|
| `databasemax.example.com` A | VM public IP |
| `www.databasemax.example.com` CNAME | `databasemax.example.com` |
| `db.databasemax.example.com` A | VM public IP |
| `*.db.databasemax.example.com` A | VM public IP (only with `DMX_DB_HOST_SUFFIX`) |

## 3. TLS certificates

- **Web/API:** Caddy obtains and renews a certificate for `DMX_DOMAIN` automatically.
- **PostgreSQL gateway:** needs a certificate for `db.<domain>` (plus `*.db.<domain>` if you use
  per-database host names, which needs a DNS-01 challenge). With DNS on Cloudflare, for example:

```sh
sudo -i
curl https://get.acme.sh | sh -s email=ops@example.com
export CF_Token="…"                     # Cloudflare API token with "Edit zone DNS" on your domain
~/.acme.sh/acme.sh --issue --server letsencrypt --dns dns_cf -d db.example.com
~/.acme.sh/acme.sh --install-cert -d db.example.com \
  --fullchain-file /etc/databasemax/gateway-certs/fullchain.pem \
  --key-file       /etc/databasemax/gateway-certs/privkey.pem \
  --reloadcmd "chown root:dmx-gw /etc/databasemax/gateway-certs/*.pem; chmod 0640 /etc/databasemax/gateway-certs/*.pem; systemctl reload dmx-gateway || true"
```

With OCI DNS use acme.sh's `dns_oci` plugin instead. The `--reloadcmd` runs on every renewal:
`systemctl reload dmx-gateway` makes the gateway re-read its certificate without dropping
connections. `deploy.sh` refuses to start a production gateway without a certificate.

## 4. Host preparation

```sh
sudo /opt/databasemax/infra/scripts/host-setup.sh
```

It installs and configures (idempotent, safe to re-run):
- unattended security upgrades, `fail2ban`, SSH keys only (`PasswordAuthentication no`,
  `PermitRootLogin no` — skip with `--no-ssh-hardening`)
- PostgreSQL 16 (the control database, unix socket only; its binaries also run the customer
  databases), Redis (loopback, no persistence), Caddy (official repository), Node.js 22,
  uv + Python 3.13 (in `/opt/databasemax-python`)
- service users `dmx-api`, `dmx-web`, `dmx-gw`, `dmx-prov` (no shell, no home)
- the systemd units from `infra/systemd/`, the `dmx-customers.slice` budget, and the host agent
  (`dmx-host-agent`, the only root service)
- the firewall rules from §2

## 5. Configuration and secrets

```sh
sudo /opt/databasemax/infra/scripts/init-env.sh --domain example.com --email ops@example.com
sudo editor /etc/databasemax/.env       # SMTP_*, DMX_REGION*, BACKUP_AGE_RECIPIENT, backups (§10)
```

`init-env.sh` generates every secret independently (`openssl rand`) and never overwrites an
existing file. `/etc/databasemax/.env` is the only file you edit; `deploy.sh` splits it into
`/etc/databasemax/env/<service>.env` (root-only), so each service receives only its own variables
— the API never sees the backup keys or the provisioner's admin secret, the gateway sees no
database or session secrets. The API refuses to start in production with development defaults,
short secrets, insecure cookies or the file email backend. Keep an offline copy of
`DMX_BACKUP_KEYS`.

**Path to OCI Vault:** store each secret in a Vault secret and render `/etc/databasemax/.env` at
boot with the OCI CLI using the instance principal (`oci secrets secret-bundle get …`).

## 6. Build and deploy

```sh
cd /opt/databasemax
sudo infra/scripts/deploy.sh v1.0.0   # from a git checkout: checks out the tag
sudo infra/scripts/deploy.sh local    # deploys the files as they are
```

`deploy.sh`:
1. checks out the tag (skipped for `local`) and renders the per-service environment files,
2. installs the unit files, builds the Python environments (`uv sync --frozen --no-dev`) and the
   web app (`pnpm build`, Next.js standalone output),
3. creates/updates the control-database roles and the Redis password from `.env`,
4. backs up the control database if it exists (`backup-control-db.sh`; needs
   `BACKUP_AGE_RECIPIENT`: run `age-keygen -o control-backup.key` on **your own machine**, keep
   that file offline, paste the public key),
5. runs the migrations (`dmx-migrate.service`, with the schema owner's credentials only),
6. restarts the services one by one — customer databases keep running, and the gateway is only
   restarted when its code or configuration changed (a restart drops open connections),
7. checks API and provisioner health and runs `smoke-test.sh` (production).

### Initial admin

Admin access is granted explicitly and audited — never by email domain:

```sh
# after registering and verifying the operator account in the web UI
sudo dmx-admin grant-admin --email ops@example.com --reason "initial operator"
```

`dmx-admin` runs `python -m app.cli` as the API's user with the API's environment.

### Upgrades and rollback

- **Upgrade:** `deploy.sh <new-tag>`. Migrations must be backward compatible with the previous
  release (expand → migrate → contract across two releases).
- **Rollback:** `deploy.sh <previous-tag> --skip-migrations`. If a migration must be reverted,
  restore the control DB backup taken by the deploy (see runbook) — never run a destructive
  downgrade on production without a backup.

## 7. Operations

- Services: `systemctl status 'dmx-*'`, `systemctl restart databasemax.target` (control plane
  only; customer databases are separate `dmx-pg@<id>` units and keep running)
- Health: `curl -fsS https://<domain>/api/v1/health`, `https://<domain>/status`
- Logs: `journalctl -u dmx-api -u dmx-worker -u dmx-gateway -u dmx-provisioner -f` (JSON;
  secrets are redacted by the application). A customer database: `journalctl -u dmx-pg@<id>`
  (no customer data is logged; statement logging is off).
- Control DB backup (daily cron): `infra/scripts/backup-control-db.sh` → encrypted with `age`,
  optionally uploaded to an OCI Object Storage bucket with a retention rule
- See `docs/deployment/runbook.md` for incident procedures.

## 8. Pre-launch verification checklist

- [ ] VM shape and memory budget match the table above
- [ ] Block volumes attached, encrypted, mounted at the expected paths
- [ ] NSG allows only 22 (admin IPs), 80, 443, 5432
- [ ] `verify-firewall.sh` run from outside the VCN passes
- [ ] DNS records resolve
- [ ] `https://<domain>` has a valid certificate; `psql "sslmode=verify-full sslrootcert=system host=db.<domain> …"` validates the gateway certificate
- [ ] `.env` has unique production secrets, SMTP configured and tested
- [ ] Paddle sandbox end-to-end test done (checkout → webhook → plan change) before switching `PADDLE_ENVIRONMENT=production`
- [ ] Control DB backup job runs and a restore was tested on a scratch VM
- [ ] `systemctl status dmx-host-agent` active; with `DMX_STORAGE_BACKEND=loop` a test database shows three loop mounts under `/var/lib/databasemax/data-plane/mnt`
- [ ] Customer backups go to OCI Object Storage (§10); one backup verified and restored from the dashboard
- [ ] Offline copy of `DMX_BACKUP_KEYS` stored outside the VM
- [ ] Monitoring/alerting (uptime check on `/api/v1/health` and TCP 5432; disk usage alerts) configured
- [ ] Admin account granted via `dmx-admin`; admin actions visible in the audit log

## 9. Customer databases and hard storage limits

Each customer database is a PostgreSQL server (`dmx-pg@<instance id>.service`, template in
`infra/systemd/dmx-pg@.service`). The host agent creates, for each one: a system user
`dmxdb-<hash>`, three filesystems (tablespace, WAL, cluster files) under
`/var/lib/databasemax/data-plane/mnt/`, the cluster (`initdb`) and a unit drop-in with its port,
binary and limits. The unit runs with no capabilities, `ProtectSystem=strict`, a private view of
`/var/lib/databasemax` that contains only its own data, other users' processes hidden, a system
call filter and no outbound network.

With `DMX_STORAGE_BACKEND=loop` the filesystems are fixed-size ext4 images, loop-mounted (hard
limit: a full database gets "No space left on device" while WAL and the cluster's own files keep
working). The agent fsck's and re-mounts every image at boot before it reports ready, and the
database units start after it. Keep `DMX_DATA_PLANE_DISK_MB` below the real size of the volume:
the budget counts full image sizes. `dir` mode uses plain directories and enforces the limit
softly (read-only switch at 100%).

## 10. Customer backups (OCI Object Storage)

1. Create a private bucket (e.g. `databasemax-backups`) in the same region; enable **object
   versioning** and a lifecycle rule that deletes versions older than your longest plan retention
   + 7 days (Pro: 21 days).
2. Create a dedicated IAM user with a policy limited to that bucket, e.g.
   `Allow group dmx-backup-writers to manage objects in compartment <c> where target.bucket.name='databasemax-backups'`,
   and generate a **Customer Secret Key** (S3 access key/secret) for it.
3. In `/etc/databasemax/.env` set:
   ```
   DMX_BACKUP_STORE=s3
   DMX_BACKUP_S3_ENDPOINT=https://<namespace>.compat.objectstorage.<region>.oraclecloud.com
   DMX_BACKUP_S3_REGION=<region>
   DMX_BACKUP_S3_BUCKET=databasemax-backups
   DMX_BACKUP_S3_ACCESS_KEY=...
   DMX_BACKUP_S3_SECRET_KEY=...
   ```
   then run `deploy.sh` again. Keep an **offline copy** of `DMX_BACKUP_KEYS` (password manager /
   OCI Vault secret). Without it no backup can be restored.
4. Verify from Admin → Backups: take a backup of a test database, run **Verify**, and restore it
   into a new database from the dashboard. Record the timing as your restore drill.
5. The S3 store has been tested against an S3 emulator only; this step is the first real test
   against OCI — do it before accepting customer data.

## 11. Adding a worker host (Phase 5)

1. Create a VM in the same VCN (private subnet, no public IP) with an encrypted block volume at
   `/var/lib/databasemax`. Put the code at `/opt/databasemax`.
2. On the worker:
   ```sh
   sudo infra/scripts/host-setup.sh --role worker        # host agent, provisioner, PostgreSQL binaries
   sudo infra/scripts/init-env.sh --worker --host-name worker-2 --private-ip 10.0.1.12 --gateway-ip 10.0.1.11
   sudo editor /etc/databasemax/.env                      # copy DMX_BACKUP_KEYS from the control-plane VM
   sudo infra/scripts/deploy.sh local --role worker
   ```
   Its provisioner and customer databases listen on the private address only; each database
   accepts customer logins only from the gateway's address.
3. On the control-plane VM, print the firewall rules and apply them **on the worker**:
   ```sh
   sudo dmx-admin firewall-plan --host worker-2 --control-ip <main private IP> --gateway-ip <main private IP>
   ```
   Mirror them in the OCI NSG: port 8090 and the range 20000-29999 open only from the main VM.
4. Register it from the control plane. The token is read from the environment and verified
   against the provisioner:
   ```sh
   sudo DMX_HOST_TOKEN=<worker PROVISIONER_TOKEN> dmx-admin register-host --name worker-2 \
     --endpoint http://<worker private IP>:8090 --region <region>
   ```
5. Check Admin → Hosts: fresh heartbeat, budgets shown. Drain the main host briefly and create a
   test database to confirm placement on the worker, then connect through the public gateway,
   take a backup, move the database back, and undrain.
