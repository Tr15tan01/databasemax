# Phase 6: feasibility and decisions

The spec lists Phase 6 candidates and asks that each be built only after its technical
feasibility and operating cost are validated, and that agent features reuse the existing project,
database, quota, authorization and audit systems. This document records the assessment behind
each decision.

**How candidates were assessed.** Each candidate was checked against four questions:
1. Can it be built on what exists (one PostgreSQL container per database, provisioner, gateway,
   row-level security owned by `owner_id`) without weakening isolation?
2. What does it add to running costs per customer and per host?
3. Can it be tested end-to-end in this environment, so we don't claim something unproven?
4. Does anything in the product need it now?

Costs are given as resource drivers, not prices. OCI prices change, so check the current price
list before committing.

## Built in Phase 6

| Feature | Why now | Extra running cost | How it reuses existing systems |
|---|---|---|---|
| **Machine identities (agent keys)** | Customers already automate with API keys, and AI agents are a growing share of callers. A key's kind and its safeguards are cheap to add. | One column on `api_keys`, and an hourly counter row per active key | Same key format, hashing, scopes, project restriction, expiry, revocation and rate limits (per key) as every key |
| **Approval workflow for destructive operations** | It is the main safety gap when a non-human caller holds delete scopes. | One table and one email per request, capped at 20 pending requests per key | Executes through the same service functions as a direct request, so the same validation, state machine, quotas and audit apply. Row-level security covers the new table. Owner notification uses the existing notification and email outbox. |
| **Agent activity dashboard** | Customers need to see what an agent did before they can trust it. | Hourly per-key counters, flushed every minute, like the existing per-user counters | Audit log filtered by `actor_api_key_id`, which was already recorded on every event |
| **Agent-specific permissions** | Covered by the existing scopes and project restriction, plus mandatory approval for agents. | None | A separate per-agent allow-list of operations was not built: scopes already express it, and a second permission system would be two places to get wrong. |

Details: `docs/reports/phase-6.md`, `apps/web/app/docs/agents`.

## Assessed and deferred

### Team collaboration and RBAC (organizations, members, roles)

- **Feasibility.** Every tenant table, every row-level security policy (`owner_id = dmx.user_id`),
  quotas, billing (one Paddle customer per user) and the audit feed are keyed to a single user.
  Teams need an `organization_id` on those tables, new policies based on membership, per-role
  checks in every router, invitations, owner transfer, and billing per organization. That is a
  migration of every tenant table plus a rewrite of the isolation tests.
- **Cost.** Negligible infrastructure cost; large engineering and risk cost. The risk is tenant
  isolation, which is the property the product relies on most.
- **Decision: deferred.** It should be its own phase, with a security review. It must not be
  bolted on. Until then, a workaround is to share project-restricted API keys, scoped per
  project and each with its own audit trail.
- **Design direction.** An `organizations` table with a `memberships(org, user, role)` join.
  Row-level security becomes `org_id IN (the user's orgs)`. Roles are `owner`, `admin`,
  `developer` and `viewer`, mapped onto the existing scope names so API keys and members share
  one permission vocabulary. Approvals move to "any admin of the org".

### Read replicas

- **Feasibility.** This needs streaming replication between two containers:
  - a replication role, and a `pg_hba` entry on the primary's private network;
  - placement of the replica on a different host;
  - lag monitoring, and a read-only endpoint in the gateway (a new SNI name);
  - the storage agent sizing both copies.

  All of it is feasible with the current pieces. None of it exists or is tested yet.
- **Cost.** Each replica uses the same memory, CPU and disk as its primary. On the current host
  budgets, that halves the number of databases a host can hold for every customer who uses one.
- **Decision: deferred.** Not built, not claimed. It would be a paid add-on and needs real
  multi-VM testing first (see the Phase 5 limitation on real VMs). Replicas would also be a
  prerequisite for any future failover claim.

### Autoscaling

- **Storage auto-grow.** This is technically ready, because online grow with an offline
  fallback exists (`resize_storage`). The open question is billing: an automatic grow changes
  what the customer pays. **Deferred until usage-based billing exists.** Customers can resize
  themselves today, and they get usage-threshold notifications.
- **Compute.** Docker can change the memory and CPU limits of a running container, but
  PostgreSQL settings derived from memory (`shared_buffers`) need a restart. Automatic restarts
  are downtime the customer didn't ask for. **Deferred.** Profile changes stay explicit.

### More regions

- **Feasibility.** Placement is already region-aware: each database stays in its region, and
  hosts carry a region. A new region needs its own gateway VM, DNS (`db-<id>.<region>.<domain>`),
  a backup bucket in that region, and at least one worker host. The control plane can stay
  central.
- **Cost.** At least one additional VM per region for the gateway and data plane, even with no
  customers there.
- **Decision: deferred until there is demand.** The code needs region-specific gateway
  hostnames; the rest is operations.

### Usage-based billing

- **Feasibility.** Metering already exists: hourly storage, connections and API requests. Paddle
  supports usage charges, but prices and invoicing rules are business decisions not in the spec.
- **Decision: deferred.** The dashboard's estimate stays labelled as an estimate of the plan
  price. We don't bill for usage we don't charge for.

### More backup options and point-in-time recovery

- WAL already has its own volume per instance. Point-in-time recovery would archive WAL
  continuously to object storage (with a tool like wal-g or pgBackRest) and restore base backup
  plus WAL to a timestamp.
- **Cost.** Object storage grows with write volume, not database size, so a busy small database
  can cost more than a large idle one. Pricing has to reflect that.
- **Decision: deferred, and not claimed anywhere.** It needs the OCI object store validated
  first; S3 is so far tested only against an emulator.

### Advanced cloning (branching)

Copies of a database already exist (restore to a new database from any backup, or from current
data through a fresh backup). Copy-on-write branching would need a filesystem with snapshots
(ZFS/btrfs) under the storage agent, a significant change to the hard-limit design.
**Deferred.**

### Advanced monitoring

Per-database metrics exist: connections, storage, the volume usage of all three filesystems,
host inventory. The next cheap step is `pg_stat_statements` for query insights, which costs a
little memory per instance and needs care to keep other roles' query text private.
**Deferred** to keep this phase focused on the safety features.

### Integrations (outbound webhooks and similar)

These overlap with approvals: an approval-requested webhook would let agents' operators route
requests to chat tools. Deliveries would need signing, retries and a dead-letter queue.
**Deferred.** The polling endpoint (`GET /v1/approvals/{id}`) covers agents today.
