# Security checklist and remediation tracker

Legend: ✅ implemented + automated test · 🟡 implemented, not (fully) tested · ⬜ not implemented

## Application
| Control | Status | Evidence |
|---|---|---|
| Argon2id password hashing, password policy | ✅ | `security/passwords.py`, `tests/test_auth.py` |
| Generic login errors, progressive throttling | ✅ | `test_login_errors_are_generic`, `test_progressive_login_throttling` |
| HttpOnly/SameSite session cookies, server-side revocation | ✅ | `test_login_logout_and_session_revocation` |
| CSRF (signed double-submit + Origin check) | ✅ | `test_csrf_required_for_cookie_auth` |
| Email verification required for databases | ✅ | `test_unverified_user_cannot_create_database` |
| Single-use expiring reset/verification tokens | ✅ | `test_password_reset_single_use…`, `test_email_verification_token_single_use` |
| Tenant isolation (API + RLS) | ✅ | `tests/test_tenant_isolation.py` |
| API key scopes, project restriction, revocation | ✅ | `tests/test_api_keys.py` |
| Admin routes reject non-admins; reauth for sensitive actions | ✅ | `tests/test_admin.py` |
| Webhook signature verification, replay/idempotency | ✅ | `tests/test_billing.py` |
| No secrets in logs / audit metadata | ✅ | `tests/test_secret_leakage.py` |
| Request size limit, security headers, request IDs | ✅ | `test_request_body_size_limit`, `test_security_headers_and_request_id` |
| Provisioning rejects infrastructure parameters | ✅ | `test_rejects_infrastructure_parameters`, provisioner `test_api_requires_token` |
| Google OAuth state/nonce/PKCE, pre-hijack defence | 🟡 | code review only — needs Google test credentials |
| Paddle checkout/portal against real API | 🟡 | needs Paddle sandbox credentials |
| 2FA / WebAuthn for admins | ⬜ | planned |

## Data plane
| Control | Status | Evidence |
|---|---|---|
| One PostgreSQL server per database under its own system user: no capabilities, read-only system, private view of its data, no outbound network, cgroup limits | 🟡 (not yet run on a Linux host) | `test_full_lifecycle` checks the unit's properties |
| pg_hba: admin only from loopback, customers only from the gateway's address | 🟡 | manual test (see Phase 1 report), provisioner validation tests |
| TLS required at gateway, SNI/username consistency | ✅ | `test_full_lifecycle`, `test_sni_must_match_username` |
| Rotation invalidates old password + sessions | ✅ | `test_full_lifecycle` |
| Deleted DB unreachable, volume removed | ✅ | `test_full_lifecycle` |
| Failed provisioning leaves no resources | ✅ | `test_failed_provisioning_cleans_up` |
| Worker crash recovery without duplicates | ✅ | `test_worker_crash_is_recovered_without_duplicates` |
| Hard storage quota per instance (loop-mounted ext4 data + WAL filesystems via the root host agent) | 🟡 (re-run after the move to systemd) | `apps/provisioner/tests/test_hard_quota_integration.py` fills a database past its allocation: `DiskFull`, server stays up, host unaffected, grow restores writes |
| Internal TLS gateway→instance | ⬜ | Phase 4 |
| AppArmor profile for customer databases | ⬜ | Phase 4 |

## Infrastructure / supply chain
| Control | Status |
|---|---|
| Only 80/443/5432 listen publicly; DMX-INPUT firewall chain from host-setup.sh | 🟡 (scripted, verify on the VM) |
| Dependency scanning (pip-audit, pnpm audit), SAST (bandit, semgrep), filesystem/lockfile scanning (Trivy), secret scanning (gitleaks) in CI | 🟡 (workflows written, run on GitHub) |
| OS packages from Ubuntu/PGDG/NodeSource/Caddy repositories with signed keyrings; unattended security upgrades | ✅ |
| Encrypted automated customer backups (keys only on provisioner, tamper-evident format, restore tests) | ✅ |
| Off-host backup storage (OCI Object Storage via S3 API) | 🟡 implemented, emulator-tested; configure + verify on OCI |
| External penetration test | ⬜ Phase 4 |
