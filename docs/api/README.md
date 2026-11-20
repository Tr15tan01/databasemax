# API documentation

- Human-readable reference: `/docs/api` in the web app (generated from the OpenAPI schema).
- Interactive: `/api/docs` (Swagger UI) on any deployment; raw schema at `/api/openapi.json`.
- The committed schema lives in `packages/shared-types/openapi.json`; TypeScript types are
  generated from it (`pnpm types:generate`). CI fails if the committed schema is stale.

## Approval responses (agent keys)

Destructive calls made with an agent key, or with a key that has `requires_approval`, answer
`202 Accepted` with header `X-DMX-Approval: pending` and this body:
`{"status": "pending_approval", "approval": {...}}`. This applies to `DELETE /v1/databases/{id}`,
`POST /v1/databases/{id}/credentials/rotate`, `DELETE /v1/projects/{id}` and
`DELETE /v1/backups/{id}`.

Clients should check `status`, because the database delete endpoint also answers `202` when it
runs directly. Poll `GET /v1/approvals/{id}` until the status is `executed`, `failed`,
`rejected` or `expired`. See `/docs/agents` in the web app.

## Database tools (dashboard)

| Endpoint | Who | Limits |
|---|---|---|
| `POST /v1/databases/{id}/sql` | signed-in session + recent password confirmation (no API keys) | 30/min per user, one running query per user, 15 s, 1,000 rows / 3 MB returned |
| `GET /v1/databases/{id}/schema` | signed-in session | 30/min per user |
| `GET /v1/databases/{id}/insights` | signed-in session | 10/min per user |
| `GET /v1/databases/{id}/live` | session or `usage:read` key | 120/min per user; one sample per database every 2 s is shared |
| `GET /v1/databases/{id}/usage?metric=transactions_per_minute` (also `rows_read_per_minute`, `rows_written_per_minute`) | `usage:read` | rates derived from cumulative counters; counter resets are skipped |

`POST /sql` body: `{"sql": "...", "mode": "read" | "write", "confirm_destructive": false, "explain": "none" | "plan" | "analyze"}`.

- `mode: "read"` (default) runs inside a read-only transaction that is always rolled back.
- In write mode, DROP, TRUNCATE, ALTER, DELETE and UPDATE answer `409 confirmation_required` with
  `details.kinds` until the request is repeated with `confirm_destructive: true`.
- Statements in one request run in one implicit transaction: if one fails, none are applied
  (unless the SQL contains its own COMMIT).
- A SQL error is not an HTTP error: the response is `200` with `error: {message, sqlstate,
  position, detail, hint}`. `position` is a 1-based character offset into the submitted SQL.
- Values are returned in PostgreSQL's text format (`null` is SQL NULL). `row_count` counts every
  row the statement produced; `truncated` says some were not returned.
