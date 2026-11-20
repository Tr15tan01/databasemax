# Phase 1 report — Secure foundation and first working database

Date: 2026-09-28 · Status: **acceptance criteria met in the development environment**, with the
caveats listed under *Known limitations*. Most Phase 2 scope was delivered at the same time
(see the end of this report).

## Acceptance criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Register and verify email | ✅ | `test_auth.py` (registration, single-use tokens); Playwright `register, verify, onboard…` |
| 2 | Log in and log out | ✅ | `test_login_logout_and_session_revocation`; Playwright sign-out/sign-in |
| 3 | Google login when configured, graceful when not | ✅ / 🟡 | Disabled path tested (`test_google_disabled_gracefully`, button hidden). Enabled path (PKCE, state, nonce, ID-token verification, pre-hijack defence) implemented but **not run against Google** — no credentials in this environment |
| 4 | Create a project | ✅ | `test_tenant_isolation.py` fixtures, Playwright onboarding |
| 5 | Provision a PostgreSQL database | ✅ | `test_full_lifecycle`; Playwright; ~5 s end to end |
| 6 | Reachable with its connection URL | ✅ | `test_full_lifecycle` (psycopg, `verify-full`), Playwright runs `psql` with the URL shown in the UI |
| 7 | Copy connection details | ✅ | Copy buttons on every field and URL; `CopyButton` unit test |
| 8 | Another user can't access it | ✅ | `test_tenant_isolation.py` (API, API keys, RLS reads/writes), `test_sni_must_match_username` (gateway) |
| 9 | Failed provisioning leaves no uncontrolled resources | ✅ | `test_failed_provisioning_cleans_up` (container + volume gone, secrets destroyed), reconciler orphan test |
| 10 | Admin can inspect users and provisioning state | ✅ | `test_admin.py`; admin console pages |
| 11 | Secrets not exposed in logs | ✅ | `test_secret_leakage.py` (captures real logs during auth flows; audit metadata; encrypted email bodies) |
| 12 | Runs locally through Docker Compose | ✅ (see note) | Full Playwright suite passed against `docker compose up`. Note: Docker Hub/GHCR were blocked in the build sandbox, so base images were substituted via `infra/sandbox/` overrides; the production Dockerfiles use official images and are built in CI |

## What was built

**Control plane (`apps/api`)** — FastAPI, SQLAlchemy 2, Alembic (2 migrations), 27 tables.
Sessions (hashed tokens, HttpOnly cookies, idle/absolute expiry, revocation), signed double-submit
CSRF + Origin checks, Argon2id, progressive login throttling (Redis, fail-closed), email
verification and password reset (single-use, expiring, fragment-delivered tokens), optional Google
OAuth, profile/preferences, account deletion, API keys (hashed, scoped, project-restricted,
expiring), audit log (append-only grants), notifications (in-app + email outbox with dedupe),
plan entitlements and transactional quota checks, idempotency keys, projects, databases, usage
metering with rollups and retention, Paddle Billing (checkout, portal, plan change, cancel,
verified idempotent webhooks, reconciliation, grace-period enforcement that never deletes), admin
API (overview KPIs, users, databases, jobs, infrastructure, audit, billing) with reauthentication
and mandatory reasons for sensitive actions, operator CLI for audited admin grants.
Row-Level Security is forced on 13 tenant tables as defence in depth.

**Provisioning** — explicit state machine (12 states, every transition validated and recorded),
PostgreSQL-backed job queue with leases, retries with exponential backoff, resumable idempotent
handlers, failure cleanup, reconciler (orphans, stuck states, endpoint drift, missing instances).

**Provisioner (`apps/provisioner`)** — only holder of the Docker socket. Allowlisted versions and
compute profiles; one container + volume per database; `cap_drop: ALL`, read-only rootfs,
`no-new-privileges`, memory/CPU/PID limits, internal networks only, no published ports; capacity
budget enforcement; per-instance admin password derived by HMAC and never stored; customer
passwords applied only as SCRAM verifiers; pg_hba restricts admin to the management subnet and
customers to the gateway subnet.

**Gateway (`apps/gateway`)** — PostgreSQL protocol: SSLRequest/TLS (1.2+), GSS decline, plaintext
rejection, SNI + username routing with consistency check, reserved-user refusal, per-database
and global connection caps, per-IP rate limit, handshake timeouts, CancelRequest routing, SIGHUP
certificate reload, private status endpoint.

**Web (`apps/web`)** — Next.js 16, Tailwind v4 design tokens (light/dark), Radix-based component
library, marketing site (landing with all 10 sections, features, pricing, security, about,
contact, privacy, terms, live status), searchable docs with an API reference generated from the
OpenAPI schema, auth pages, 5-step onboarding with live provisioning progress, dashboard with
real metrics and charts, projects, databases (overview/connection/usage/settings/backups/activity),
usage, billing, API keys, activity, settings, support, and the admin console.

## Commands

See `README.md`. Key ones:
`infra/scripts/dev-services.sh`, `infra/scripts/dev-host.sh start`, `docker compose up -d`,
`uv run pytest`, `pnpm --filter @databasemax/web test:e2e`, `infra/scripts/deploy.sh <tag>`.

## Tests executed (final run)

| Suite | Result |
|---|---|
| API pytest (incl. 4 Docker integration tests) | 76 passed |
| Provisioner pytest | 24 passed |
| Gateway pytest | 10 passed |
| Web unit tests (Vitest) | 16 passed |
| Playwright E2E — host processes | 4 passed |
| Playwright E2E — Docker Compose stack | 4 passed |
| `tsc --noEmit`, ESLint, `next build` | clean |
| ruff (lint + format), mypy (3 Python services) | clean |

## Security checks performed

- Manual data-plane checks: customer login from management subnet rejected; `dmx_admin` from
  gateway subnet rejected; customer role not superuser; `COPY … TO PROGRAM` denied; no access to
  `postgres` database; plaintext connections rejected; wrong SNI rejected; connection cap enforced;
  query cancel works through the gateway.
- Bandit: 0 high/medium findings after review (4 reviewed false positives annotated).
- pip-audit (3 services) and `pnpm audit --prod`: no known vulnerabilities at time of writing.
- Threat model: `docs/security/threat-model.md`; checklist with evidence: `docs/security/checklist.md`.
- **Not done:** independent review / penetration test; Semgrep, gitleaks and Trivy run only in CI
  (not executed in this environment).

## Known limitations

1. **No automated customer backups** yet (Phase 4). The UI, docs and pricing say so.
2. Storage limits are **soft** (measured; DB switched to read-only by default when exceeded).
3. Gateway → instance traffic is plaintext on an internal network (TLS terminates at the gateway);
   `channel_binding=require` is unsupported.
4. Single host; no HA, no failover, no replicas.
5. Paddle and Google integrations untested against live services; Paddle.js checkout needs
   sandbox credentials to verify.
6. Compute size changes require recreate + restore (not in the UI yet).
7. Base images pinned by tag, not digest.
8. Direct-TLS (`sslnegotiation=direct`, PG17 clients) not supported by the gateway.

## Phase 2 status (delivered alongside Phase 1)

| Phase 2 item | Status |
|---|---|
| Full dashboard, project management, database settings | ✅ |
| Credential rotation invalidating old credentials + sessions | ✅ tested |
| Usage monitoring (measured vs allocated vs estimated, per project/database, history, thresholds) | ✅ |
| API documentation matching routes (generated; CI diff check) | ✅ |
| Versioned REST API, API keys with scopes and revocation | ✅ tested |
| Activity history, notifications (in-app + email, preferences, dedupe) | ✅ |
| Database health checks (metrics collector + reconciler events) | ✅ |
| Server-side quotas, search/filter/sort/pagination, responsive UI, dark mode | ✅ |
| Cross-tenant tests | ✅ |

## Next phase checklist (Phase 3 → 4)

- [ ] Backup engine: `pg_dump` via provisioner, encrypted stream, pluggable storage (local + OCI Object Storage), checksums, retention
- [ ] Restore into a new database; restore verification job
- [ ] Database cloning (backup-and-restore) with independent credentials and quota accounting
- [ ] Backups tab and admin backup dashboard wired to real data; notifications for failures
- [ ] Hard storage quotas (XFS project quotas) and internal TLS
- [ ] External security review with tracked remediation list
