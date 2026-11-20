# Incident response

## Severity
- **SEV1** — confirmed customer data exposure, cross-tenant access, credential leak, or full outage.
- **SEV2** — partial outage (provisioning or gateway down), suspected compromise under investigation.
- **SEV3** — degraded performance, single-customer issue.

## First 30 minutes (SEV1/SEV2)
1. Declare the incident, name an incident lead, start a timeline (UTC times, who did what).
2. **Contain**, preferring reversible steps:
   - suspected account compromise → Admin → Users → Suspend (revokes sessions);
   - leaked API key → revoke it (users can, admins can via DB if needed);
   - leaked database credential → rotate (customer or support) — old password stops working immediately;
   - suspected platform secret leak → rotate per runbook;
   - active attack on the gateway → restrict 5432 in the OCI NSG to known sources.
3. **Preserve evidence** before changing more: `journalctl -u <unit>` of affected services, audit events
   (`SELECT … FROM audit_events WHERE created_at > …`), login attempts, gateway counters,
   `systemctl show dmx-pg@<instance>` of affected instances. Copy to the incident folder (encrypted).
4. Communicate on the status page; don't speculate about causes.

## Detection (Admin → Security, Admin → Infrastructure alerts)

| Signal | Threshold | First response |
|---|---|---|
| Failed sign-ins platform-wide | ≥ 100 / hour | Check top sources; tighten `DMX_RATE_LIMIT` / block at the NSG if one network dominates |
| Failed sign-ins on one account | ≥ 20 / hour | Progressive throttling is automatic; contact the owner if it persists |
| Failed sign-ins from one IP | ≥ 50 / hour | Consider blocking the source |
| Password reveals by one user | ≥ 50 / 24 h | Check the user's API keys and sessions (possible scraping with a stolen session/key) |
| Admin role granted | any | Confirm with the granting operator (CLI-only, audited) |
| Backup restore verification failed | any | SEV2: keep the backup, re-verify, test a restore as the customer would |
| Host agent unreachable / data-plane disk ≥ 80 % | any | See runbook "Host agent / full volumes" |

Thresholds live in `apps/api/app/monitoring/security.py`.

## Investigation aids
- Audit log: every sensitive action with actor type (user / api_key / admin / system), request ID and IP.
- Login attempts table: failures, throttling, new-network sign-ins.
- Provisioning jobs and database events: lifecycle history per database.
- Logs are JSON with request IDs; secrets are redacted at the logging layer.

## Recovery and follow-up
- Restore service; verify with the smoke test.
- Notify affected customers without undue delay, and authorities where legally required
  (e.g. GDPR: 72 hours after becoming aware of a personal-data breach).
- Blameless post-incident review within 5 business days; track remediation items in the
  security remediation list (`docs/security/checklist.md`, `docs/security/review-2026-09.md`).
