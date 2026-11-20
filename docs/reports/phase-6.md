# Phase 6 report: agent identities, approvals, and a feasibility review of advanced features

Date: 2026-09-29. Decisions and cost reasoning: `docs/architecture/phase-6-feasibility.md`.

## Scope decision

The spec says to build Phase 6 candidates "only after validating technical feasibility and operating costs", and requires agent features to reuse the existing project, database, quota, authorization and audit systems.

**Built**, because they are cheap to run, close a real safety gap and are fully testable here:
- machine identities (agent keys);
- agent-specific permissions (existing scopes and project restriction, plus mandatory approval);
- approval workflows for destructive operations;
- agent activity and audit dashboards.

**Assessed and deferred**, with reasons, in the feasibility document:
- team collaboration and RBAC;
- read replicas;
- autoscaling;
- more regions;
- usage-based billing;
- point-in-time recovery and more backup options;
- branching;
- query-level monitoring;
- outbound integrations.

None of the deferred features is claimed anywhere in the product or docs.

## Acceptance checks

| Requirement | Status | Evidence |
|---|---|---|
| Machine identities | ✅ | `api_keys.kind` is `standard` or `agent` (check constraint), with an optional description. Agent keys are created from the Agents page or the API key dialog. They use the same hashing, scopes, project restriction, expiry, revocation and per-key rate limit as other keys. |
| Agent-specific API permissions | ✅ | Existing scopes and project restriction, plus approvals that agent keys can't turn off (400 if attempted). `test_agent_key_always_requires_approval`. |
| Approval workflow for destructive operations | ✅ | Covers deleting a database, a project or a backup, and rotating a password. The request is validated first, then stored and answered `202 pending_approval`. Only a signed-in owner session with recent re-authentication can approve. Execution goes through the same service functions. See `test_agent_project_delete_waits_for_approval_then_runs`, `test_database_delete_gated_and_executed_through_normal_path` and the E2E `agents.spec.ts`. |
| Agent activity and audit dashboard | ✅ | Agents page: requests per key (24 h and 7 days, hourly chart), changes, failures, last change, pending approvals, recent changes. The activity log can be filtered by API key. See `test_per_key_activity_and_audit_filter`. |
| Reuse of existing systems, no bypasses | ✅ | Approved operations call `request_delete`, `request_rotation`, `delete_backup` and `delete_project_record` as the approving user, so the state machine, plan entitlements, quotas, audit and notifications all apply. No new path to the provisioner exists. |
| Features validated for feasibility and cost | ✅ | `docs/architecture/phase-6-feasibility.md`. |

## Implemented

- **Data model** (migration `0006`):
  - `api_keys.kind`, `requires_approval` and `description`.
  - `approval_requests`: owner, key, action, resource, summary, parameters, status, expiry, decider, reason, result.
  - `api_key_request_counters`: hourly count per key.
  - Both new tables have forced row-level security and `dmx_app` grants.
- **`app/services/approvals.py`**:
  - `gate()` is called after an endpoint's own validation. For keys that need approval, it reuses an identical pending request or creates one, capped at 20 pending per key. It audits `approval.requested`, notifies the owner with an essential notification (always emailed), commits, and raises `ApprovalRequired`, which becomes a `202` response.
  - `approve()` runs the operation inside a savepoint. A failed run is recorded as `failed` with the reason and changes nothing.
  - `reject()`.
  - Requests expire lazily after 24 hours.
- **API**:
  - `GET /v1/approvals` (filters: `status`, `decided`, `api_key_id`) and `GET /v1/approvals/{id}`: API keys see only their own requests.
  - `GET /v1/approvals/count`.
  - `POST /v1/approvals/{id}/approve` and `POST /v1/approvals/{id}/reject`: session only; approving also needs recent re-authentication.
  - `PATCH /v1/api-keys/{id}` (approval setting and description; turning approval off needs re-authentication).
  - `GET /v1/api-keys/activity` (per-key usage).
  - `GET /v1/activity?api_key_id=`.
- **Per-key request counting**: the Redis hourly hash gets `k:<key>:<user>` fields. The worker flushes them every minute into `api_key_request_counters`.
- **Web**:
  - **Agents page**: summary stats, per-agent cards with an hourly chart, recent changes, and an integration snippet.
  - **Approvals page**:
    - pending cards with approve (with re-authentication) and reject (with an optional note);
    - a history of decided requests, filtered by outcome;
    - deep links from emails (`?id=`);
    - a sidebar badge with the pending count.
  - **API key dialog**: agent and approval switches, and a description field.
  - **Activity log**: a filter by API key and the key name on each row.
  - **Docs page**: `/docs/agents`.
  - Dialogs now scroll when taller than the viewport. The E2E test found this bug: the longer key dialog pushed its submit button off-screen.

## Tests and results

| Suite | Result |
|---|---|
| API pytest | **113 passed** (11 new in `test_approvals.py`) |
| Provisioner pytest | **58 passed** (unchanged this phase) |
| Gateway pytest | **13 passed** |
| Web unit (Vitest) | **21 passed** (3 new: agent dialog, labels) |
| Playwright E2E | **6 passed** (new: `agents.spec.ts`) |
| tsc, ESLint, ruff, mypy | clean |

The new API tests cover:
- the full lifecycle, including a repeated request reusing the pending approval, the notification, the agent polling, and the agent being unable to approve;
- rejection;
- validation happening before a request is created;
- standard keys staying unchanged unless they opt in;
- database deletion through the normal job path (the delete job's reason references the approval);
- failed execution leaving state intact;
- expiry;
- isolation between tenants and between keys;
- per-key counters and the activity filter;
- the cap on pending requests per key.

## Security checks

- **No API key can approve anything**: approving needs a dashboard session and recent re-authentication, checked twice (router dependency and service).
- **A pending request changes nothing.** The endpoint's own validation (scope, project restriction, confirmation name, state) runs before an approval is created, so invalid requests never reach the owner.
- **Execution runs as the owner** through the normal services, so the owner's plan limits and state rules apply at the time of approval, not at the time of the request.
- **Tenant isolation**: row-level security on `approval_requests` and `api_key_request_counters`, plus explicit owner filters. Keys see only their own requests (tested).
- **Abuse limits**: at most 20 pending requests per key, deduplication per resource, the existing per-key and per-user write rate limits, and one notification per request (deduplicated).
- **Audit trail**: requested, approved, rejected, and the operation itself are recorded, with the key id and the approver.
- **No secrets** in approval records: they hold resource ids, a human summary and non-secret parameters (`final_backup`).

## Known limitations

1. **Summaries are a snapshot.** An approval shows the resource name at request time. If the resource is renamed before approval, the approval still targets the same id.
2. **Only four operations are gated.** Stopping a database and archiving a project are reversible and are not gated.
3. **Agents must poll.** There is no outbound webhook yet (deferred, see the feasibility document).
4. **Up to a minute of delay** in per-key request counts, because they are flushed every minute.
5. **Teams and RBAC are not built.** Approvals go to the account owner only.
6. The Phase 5 open items still apply:
   - multi-host not yet run on real VMs;
   - S3 backups not yet tested against real OCI Object Storage;
   - internal TLS;
   - two-factor authentication for admins;
   - an external penetration test.

## Next steps (after the six phases)

- [ ] Deploy to OCI and run the multi-VM checklist (`docs/deployment/oci.md` §11) and the OCI Object Storage backup test.
- [ ] Admin two-factor authentication (WebAuthn) and internal TLS between the gateway and instances.
- [ ] External penetration test.
- [ ] Teams and RBAC as a dedicated phase with its own security review.
- [ ] Outbound webhooks for approval requests.
