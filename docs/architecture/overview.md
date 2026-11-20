# DatabaseMax — Architecture Overview

Status: Phase 1 design, written before implementation and kept in sync with the code.
Scope: a single Oracle Cloud (OCI) Ubuntu VM at launch, with abstractions that allow
multiple data-plane hosts later. Everything runs as systemd services (Docker was removed in
November 2026; earlier phase reports describe the container-based design).

## 1. Components and trust boundaries

```
                 Internet
                    │
     ┌──────────────┼────────────────────────────────────────────┐
     │ :443 (HTTPS)  │                         :5432 (PostgreSQL+TLS)
     ▼              │                                 ▼
 ┌────────┐         │                          ┌──────────────┐
 │ Caddy  │ TLS, security headers              │ dmx-gateway  │ TLS termination,
 └───┬────┘                                    │ (Python)     │ SNI/username routing,
     │ /api/*          /*  (127.0.0.1)         └──────┬───────┘ per-DB connection caps
     ▼                  ▼                             │ reads routes from internal API
 ┌────────┐       ┌──────────┐                        │ 127.0.0.1:<instance port>
 │  API   │       │ Next.js  │                        ▼
 │FastAPI │       │   web    │               ┌──────────────────┐
 └───┬────┘       └──────────┘               │ customer PG      │ one PostgreSQL server
     │  control DB (unix socket only)         │ dmx-pg@<id>      │ per customer database:
     ▼                                        │ systemd units    │ own user/storage/limits
 ┌────────────┐   ┌───────┐   ┌─────────┐     └────────▲─────────┘
 │ control PG │◄──│worker │──►│  Redis  │              │ dmx_admin over loopback
 └────────────┘   └───┬───┘   └─────────┘              │
                      │ bearer token, allowlisted ops   │
                      ▼                                 │
                ┌─────────────┐                         │
                │ provisioner │─────────────────────────┘
                └──────┬──────┘
                       │ unix socket, closed operation set
                ┌──────▼──────┐  system users, filesystems, dmx-pg@ units
                │ host agent  │  (the only root service)
                └─────────────┘
```

| Boundary | What crosses it | Control |
|---|---|---|
| Browser → Caddy → API | JSON over HTTPS, session cookie | TLS, HttpOnly/Secure/SameSite cookies, CSRF double-submit + Origin check, rate limits, request size limits |
| API client → API | `Authorization: Bearer dmx_…` API keys | hashed keys, scopes, optional project restriction, revocation |
| API/worker → control DB | SQL as `dmx_app` | parameterized SQLAlchemy, Row-Level Security (defense in depth), unix socket only |
| worker → provisioner | allowlisted JSON operations | shared bearer token, loopback (or firewalled private address), strict input validation, no shell |
| provisioner → host agent | closed set of operations over a unix socket (root + `dmx-prov` group) | the agent re-validates every value (instance ids, versions, ports, CIDRs, bounded integer settings, SCRAM verifiers); fixed argv, no shell; it renders the unit and config files itself |
| provisioner → customer PG | `dmx_admin` over loopback | admin password derived per instance by HMAC, never stored or sent to the API; pg_hba allows `dmx_admin` only from 127.0.0.1 |
| Internet client → gateway → customer PG | PostgreSQL wire protocol | TLS mandatory, route by SNI/username, SCRAM auth performed end-to-end by the customer's own PostgreSQL, `dmx_admin`/`dmx_*`/`postgres` rejected at the gateway |
| gateway → internal API | route lookup | separate internal FastAPI app on loopback, shared token, never routed by Caddy |

The control plane never stores customer application data. Customer data lives only in each
database's own filesystems under `/var/lib/databasemax/data-plane`.

## 2. The seven Phase 1 decisions

### 2.1 Routing customer PostgreSQL connections

**Decision: a TLS-terminating PostgreSQL gateway on one public port (5432), routing by TLS SNI
and by the globally unique username in the StartupMessage.**

- Every database gets a route id (`db-` + 10 base32 chars) and a globally unique login role
  (`u_<route id>`). Its public host is `<route id>.<DMX_DB_HOST_SUFFIX>` (wildcard DNS +
  wildcard certificate), or `DMX_DB_PUBLIC_HOST` when wildcard DNS isn't set up.
- The gateway speaks the PostgreSQL protocol: it answers `SSLRequest` with `S`, performs the
  TLS handshake (reading SNI), then reads the StartupMessage. Plaintext startup is rejected with
  a PostgreSQL `ErrorResponse` telling the client to use `sslmode=require`. GSSAPI encryption
  requests are declined.
- Route selection: SNI host (if it matches the suffix) **and** username must agree; if the client
  sent no SNI (IP address hosts, older drivers) the username alone selects the route.
  A mismatch, an unknown route, a reserved username (`dmx_admin`, `postgres`, `pg_*`) or a
  database that isn't `ready`/`testing` produces a generic error (no enumeration).
- Authentication is **not** done by the gateway. The StartupMessage is forwarded verbatim to the
  customer's own PostgreSQL, which performs SCRAM-SHA-256 with the per-database credential.
  The gateway never sees the password.
- The gateway enforces a per-database concurrent connection cap from the plan, a per-IP
  connection-attempt rate limit and idle/handshake timeouts. PostgreSQL also enforces
  `max_connections` and a role `CONNECTION LIMIT`.
- CancelRequests are routed using the BackendKeyData the gateway observed for the session.
- Direct-TLS negotiation (PG17 `sslnegotiation=direct`) is not supported yet; standard
  `sslmode=require` / `verify-full` work with every libpq-based driver.

Rejected alternatives: one public port per database (large firewall surface, port exhaustion,
no hostname-based certificates); an HTTP reverse proxy (cannot proxy the PostgreSQL protocol);
Caddy/nginx `stream` SNI routing (PostgreSQL's in-band `SSLRequest` precedes the ClientHello, so
generic SNI routers don't work without direct-TLS).

### 2.2 Tenant isolation

- **Control plane:** every tenant-owned query goes through repository functions that filter by the
  authenticated owner. IDs from the client are never trusted for ownership: resources are loaded
  with `WHERE id = :id AND owner_id = :current_user`, and a miss is a 404 (no existence leak).
- **Row-Level Security** is enabled and forced on tenant tables (`projects`, `databases`,
  `database_credentials`, `provisioning_jobs`, `api_keys`, `notifications`, `usage_records`,
  `audit_events`). The session sets `dmx.user_id` per transaction; system code paths (worker,
  admin) explicitly set `dmx.bypass_rls`. A forgotten filter therefore returns no rows instead
  of another tenant's rows.
- **API keys** carry scopes and an optional project allowlist; machine identities are a separate
  actor type (`api_key`) in authorization and audit logs.
- **Data plane:** one PostgreSQL server per customer database, run as the systemd unit
  `dmx-pg@<instance id>` (template `infra/systemd/dmx-pg@.service`) under its **own system user**
  `dmxdb-<hash>`, with its own filesystems and credentials, CPU/memory/task limits in
  `dmx-customers.slice`, no Linux capabilities, `NoNewPrivileges`, `ProtectSystem=strict`, a
  system-call filter, `ProtectProc=invisible` (other users' processes are hidden), a private view
  of `/var/lib/databasemax` that contains only its own data (`TemporaryFileSystem` + `BindPaths`),
  and `IPAddressDeny=any` except loopback (and the gateway host on worker VMs): it listens on
  127.0.0.1 only and cannot open outbound connections.
- Cross-tenant authorization tests are part of the test suite (`tests/test_tenant_isolation.py`).

### 2.3 Provisioning without exposing infrastructure control

- The public API only writes intent (a `databases` row plus a `provisioning_jobs` row) after
  validating plan quotas. It never touches the data plane.
- A **worker** claims jobs from PostgreSQL with `FOR UPDATE SKIP LOCKED` and a lease; Redis is a
  wake-up signal only, so jobs survive Redis/worker restarts. Expired leases are reclaimed
  (reconciliation), retries use exponential backoff, each step has a timeout.
- The worker calls the **provisioner**, a separate unprivileged service (`dmx-prov`). Its API is
  a closed set of operations (ensure/configure/rotate/stop/start/destroy/stats/list) with strict
  validation: instance ids match `^[a-z0-9]{12,32}$`, PostgreSQL versions and compute profiles
  come from allowlists mapped to installed binaries and fixed limits, identifiers are
  regex-validated and quoted with `psycopg.sql`. No shell commands are built from input.
- Everything that needs root (system users, filesystems, unit files, `initdb`) is done by the
  **host agent** (`apps/provisioner/dmx_host_agent`, standard library only), reachable only on a
  unix socket owned by root and the provisioner's group. It re-validates every value and renders
  the unit drop-in, `pg_hba.conf` and the server settings itself from bounded integers — the
  provisioner can't pass paths, options or commands.
- The state machine (`pending → validating → queued → provisioning → configuring → testing →
  ready`, plus `failed`, `stopping`, `stopped`, `deleting`, `deleted`) is enforced in one module;
  every transition is validated and written to `database_events`.
- A final failure destroys any unit, filesystem and system user created for the attempt. A
  periodic reconciler lists provisioner-managed instances and removes orphans that have no live
  database row.
- Duplicate requests are prevented by `Idempotency-Key` handling and a unique
  `(project_id, name)` constraint for live databases.

### 2.4 Storing and rotating database credentials

- Customer passwords are 32 random alphanumeric characters generated by the worker.
- PostgreSQL receives only a **SCRAM-SHA-256 verifier** (`ALTER ROLE … PASSWORD 'SCRAM-SHA-256$…'`),
  so the plaintext never reaches the provisioner, PostgreSQL logs or `pg_authid`.
- The plaintext is stored encrypted (AES-256-GCM, 96-bit random nonce, AAD bound to the
  credential id) in a dedicated `secrets` table, referenced from `database_credentials.secret_reference`.
  Keys come from `DMX_SECRETS_KEYS` with key ids, so keys can be rotated; the `SecretStore`
  interface is where an OCI Vault backend plugs in.
- Rotation creates a new credential record, sets the new verifier, terminates existing sessions
  of that role and marks the previous credential `revoked_at`. The old password stops working
  immediately (tested).
- The admin password for each instance is `HMAC-SHA256(PROVISIONER_ADMIN_SECRET, instance_id)`,
  computed only inside the provisioner; the control plane never knows it. Administrators cannot
  see customer passwords through the admin panel.
- Revealing a password is an explicit, audited action; passwords are never logged (log filter +
  tests).

### 2.5 Partitioning the single OCI VM

- Reserved for the control plane (Caddy, web, API, worker, provisioner, gateway, control PG,
  Redis): fixed `MemoryMax`/`CPUQuota` in each unit file (`infra/systemd/`).
- Customer databases run in `dmx-customers.slice`, capped as a whole at the data-plane budget,
  and the provisioner refuses to place a database if the sum of allocated memory would exceed
  `DMX_DATA_PLANE_MEMORY_MB` or the volume disk would exceed `DMX_DATA_PLANE_DISK_MB`.
- Recommended minimum: 4 OCPU / 24 GB (A1.Flex) — 6 GB reserved for the control plane, the rest
  for customer databases; see `docs/deployment/oci.md` for capacity numbers and warnings.
- **Phase 5:** more capacity comes from worker VMs, each running only a provisioner, the
  host agent and customer instances (`host-setup.sh --role worker`). The control plane keeps
  a host registry and places each new database on the host with the most headroom; the single
  gateway still fronts every database. Moves between hosts are explicit, and a failed host is
  reported, never silently replaced. Details: `docs/architecture/multi-node.md`.
- Customer volumes live on a dedicated, encrypted OCI Block Volume mounted at
  `/var/lib/databasemax/data-plane`; control-plane data on a separate volume.

### 2.6 Backups

- Implemented (Phases 3–4): daily logical `pg_dump -Fc` per database, run by the provisioner
  with the binaries of the instance's own PostgreSQL version (fixed argv, loopback, minimal
  environment), streamed through chunked
  AES-256-GCM with keys held only by the provisioner, stored on the VM (`local`) or an
  S3-compatible bucket such as OCI Object Storage (`s3`). Retention by plan, SHA-256 +
  per-chunk authentication checked before every restore, scheduled restore tests into scratch
  instances with no customer network path. Restores always create a new database (clones).
  No point-in-time recovery is advertised. Details: `docs/architecture/backups.md`.
- Client binaries always match the server version: both come from the host's
  `postgresql-<version>` package (`/usr/lib/postgresql/<version>/bin`).

### 2.7 Enforcing plan limits

- Plans and entitlements live in the `plans` table (JSON entitlements), seeded by migration and
  editable by admins; Paddle price ids are mapped separately through environment variables.
- Every create operation checks entitlements server-side inside the same transaction that
  inserts the row (`SELECT … FOR UPDATE` on the user's subscription row serializes concurrent
  requests): projects, databases, total allocated storage, per-database connections, allowed
  compute profiles and concurrent provisioning jobs.
- Runtime: CPU/memory/tasks via the instance unit's cgroup limits, connections via gateway cap + PostgreSQL
  `max_connections` + role `CONNECTION LIMIT`.
- Storage, two layers:
  1. **Soft (billing) limit** — measured with `pg_database_size`; above 100% of the allocation the
     database is switched to `default_transaction_read_only` and the user is notified.
  2. **Hard limit** (`DMX_STORAGE_BACKEND=loop`, default in production) — every instance gets
     three fixed-size ext4 filesystems from the root **host agent**
     (`apps/provisioner/dmx_host_agent`: systemd unit, unix socket, closed operation set):
     - **tablespace** `dmx_data`: the customer database's default tablespace, sized at the
       allocation + 64 MB + 5 %. The customer has no CREATE on `pg_default`.
     - **WAL**: ≥ 320 MB, or 3× `max_wal_size` + 128 MB, with `max_wal_size` scaling with the
       allocation.
     - **PGDATA**: 384 MB, for cluster-wide files (pg_xact, pg_logical, `postgres` db).

     A tenant that writes past its allocation gets `could not extend file … No space left on
     device` errors. It can never consume host disk or affect other tenants. Checkpoints, WAL and
     the cluster's own bookkeeping keep working, so a full database does not crash the server.
     Images are sparse; the provisioner's disk budget counts their real (allocated) sizes, so the
     host can't be overcommitted.
  - Growing an allocation grows the filesystem online (`resize2fs`, needs `CAP_SYS_RESOURCE`) or,
    where the kernel refuses, by a brief stop → offline grow → start. Shrinking keeps the larger
    filesystem (the soft limit still applies). The original design called for XFS project
    quotas; loop-mounted per-instance filesystems were chosen because they need no special
    host filesystem, give exact per-instance limits (including WAL isolation) and are testable
    on any Linux host.

## 3. Request lifecycle — creating a database

1. `POST /api/v1/databases` (cookie + CSRF, or API key with `databases:write`), optional
   `Idempotency-Key`.
2. API: validate body (name, version from allowlist, profile allowed by plan, storage within
   quota), lock subscription row, insert `databases` (`pending → validating → queued`) and a
   `provisioning_jobs` row, write audit event, push wake-up to Redis. Returns `202` with the
   database resource.
3. Worker: claims job → `provisioning` (provisioner `PUT /v1/instances/{id}`) → `configuring`
   (generate password, store encrypted, send SCRAM verifier; create role/database) → `testing`
   (connect through the real gateway with TLS using the customer credential, `SELECT 1`) →
   `ready`; notification + audit event.
4. UI polls `GET /api/v1/databases/{id}` and shows the stage; on `ready` the connection page
   shows host/port/db/user, the URL (password masked until revealed), SSL requirements and examples.

## 4. Code layout

```
apps/api          FastAPI control plane (public API, internal API, worker, CLI)
apps/provisioner  Data-plane service + the root host agent (dmx_host_agent)
apps/gateway      PostgreSQL TLS gateway
apps/web          Next.js 16 frontend (marketing, docs, dashboard, admin)
infra/            systemd units, Caddy, host setup/deploy scripts, backups
docs/             architecture, security, deployment, api
packages/shared-types  TypeScript types generated from the FastAPI OpenAPI schema
```
