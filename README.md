# DatabaseMax

Managed PostgreSQL for developers: create a database, get a connection URL, start building.

This monorepo contains a working SaaS: control plane (FastAPI), a restricted infrastructure
provisioner, a TLS PostgreSQL gateway, a Next.js 16 web app (marketing site, docs, dashboard,
admin console) and deployment tooling for an Ubuntu VM. Everything runs under **systemd** —
no Docker: each customer database is its own PostgreSQL server (`dmx-pg@<id>.service`) with its
own system user, cgroup limits and sandbox.

```
apps/api            FastAPI control plane: public API, internal API, worker, operator CLI
apps/provisioner    Decides what runs on a data-plane host; the only client of the host agent
  dmx_host_agent    The only root service: system users, per-database filesystems, dmx-pg@ units
apps/gateway        PostgreSQL wire-protocol gateway: TLS, SNI/username routing, connection caps
apps/web            Next.js 16 app (App Router, Tailwind v4, Radix/shadcn-style components)
packages/shared-types  TypeScript types generated from the API's OpenAPI schema
infra/systemd       Unit files (control plane, host agent, dmx-pg@ template for customer databases)
infra/scripts       host-setup.sh, init-env.sh, deploy.sh, dmx-admin, backups, smoke test
infra/proxy         Caddy configuration
docs/               architecture, security, deployment, API, phase reports
```

Start with `docs/architecture/overview.md` (design decisions) and `docs/reports/` (what is done,
tested and still open, per phase).

## Run it

The same three commands install it on an Oracle Cloud VM, any Ubuntu 24.04 VPS, a local VM or
WSL with systemd enabled. Put the code at `/opt/databasemax` first.

```sh
sudo infra/scripts/host-setup.sh                 # packages, users, PostgreSQL, Redis, Caddy, units, firewall
sudo infra/scripts/init-env.sh --dev             # or: --domain example.com --email you@example.com
sudo infra/scripts/deploy.sh local               # build, migrate, start (re-run to update)
```

With `--dev` the web app and API are on http://localhost (API docs at `/api/docs`), the gateway
uses a self-signed certificate for `localhost` / `*.db.localhost`, and verification emails are
readable at `curl localhost/api/v1/dev/mailbox` (this endpoint does not exist in production).
Customer databases are reachable through the gateway on port 5432; `*.db.localhost` resolves to
127.0.0.1 on most systems, otherwise add `hostaddr=127.0.0.1` to the URL.

Operate it with systemd:

```sh
systemctl status 'dmx-*'                         # control plane + one dmx-pg@<id> per customer database
journalctl -u dmx-api -u dmx-worker -f           # logs
sudo systemctl restart databasemax.target        # restart the control plane (databases keep running)
```

For web UI work, `pnpm --filter @databasemax/web dev` serves the app with hot reload on :3000
against the installed API.

### Test accounts without email verification

```sh
sudo DMX_USER_PASSWORD='a-long-test-passphrase' dmx-admin create-user --email tester1@example.com --name "Tester 1"
sudo dmx-admin verify-user --email someone@example.com --reason "QA"   # an account that registered normally
```

Both are audited. Admins can also press **Mark email verified** on Admin → Users → (user).
There is deliberately no setting that turns verification off for public sign-ups.

Grant yourself admin (explicit and audited):
`sudo dmx-admin grant-admin --email you@example.com --reason "local dev"`.

## Tests

```sh
sudo infra/scripts/dev-services.sh            # throwaway PostgreSQL :55432 + Redis :56379 for unit tests
(cd apps/api && uv run pytest)                # integration tests need a host running the provisioner
(cd apps/provisioner && uv run pytest)        # data-plane tests: sudo DMX_HOST_TESTS=1 uv run pytest
(cd apps/gateway && uv run pytest)
pnpm --filter @databasemax/web typecheck && pnpm --filter @databasemax/web lint && pnpm --filter @databasemax/web test
pnpm --filter @databasemax/web test:e2e       # against a running stack (E2E_BASE_URL, E2E_GATEWAY_ADDR)
```

CI (`.github/workflows/ci.yml`) runs the unit tests, then installs the whole platform on the
runner with the same scripts as a VPS and runs the data-plane, integration and browser tests
against it, plus pip-audit, pnpm audit, Bandit, Semgrep, gitleaks and Trivy.

## Deploy

See `docs/deployment/oci.md` (capacity, firewall rules, DNS, TLS, secrets, deploy/upgrade/
rollback) and `docs/deployment/runbook.md`.

## Honest status

Implemented and tested: provisioning, TLS gateway, dashboard/admin, API keys, usage, billing
logic, **encrypted daily backups, restore-to-new-database clones, restore verification** and
**hard per-database storage limits** (see `docs/reports/phase-3-4.md`).

Multiple data-plane hosts (Phase 5): host registry, capacity-aware placement, explicit moves
between hosts and failure visibility — tested with two provisioners on one machine
(`docs/reports/phase-5.md`, `docs/architecture/multi-node.md`).

Agents (Phase 6): agent API keys (machine identities), human approval for destructive
operations requested by keys, per-key activity dashboards (`docs/reports/phase-6.md`). Other
Phase 6 candidates were assessed and deferred with reasons in
`docs/architecture/phase-6-feasibility.md`.

Not implemented / not claimed: teams/RBAC, read replicas, autoscaling, point-in-time recovery,
HA/automatic failover, live migration.
Paddle and Google OAuth are implemented but not exercised against live credentials; the S3 backup
store is tested against an emulator, not yet against OCI Object Storage. Security review and
remediation list: `docs/security/review-2026-09.md`.
