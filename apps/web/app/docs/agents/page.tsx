import { CodeBlock } from "@/components/ui/code-block";
import { Callout, DocHeader } from "@/features/docs/doc-page";

export const metadata = { title: "Agents & approvals" };

const PENDING = `DELETE /api/v1/databases/{id}?confirm=orders
Authorization: Bearer dmx_live_…

HTTP/1.1 202 Accepted
X-DMX-Approval: pending

{
  "status": "pending_approval",
  "approval": {
    "id": "7c1e…",
    "action": "database.delete",
    "summary": "delete database “orders”",
    "status": "pending",
    "expires_at": "2026-09-30T10:12:00Z",
    "approve_url": "/approvals?id=7c1e…"
  }
}`;

const POLL = `import time, requests

def wait_for_decision(api, key, approval_id, timeout=3600):
    """Poll until the owner decides. Returns the final approval object."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        a = requests.get(f"{api}/v1/approvals/{approval_id}",
                         headers={"Authorization": f"Bearer {key}"}).json()
        if a["status"] != "pending":
            return a          # executed | failed | rejected | expired
        time.sleep(30)
    raise TimeoutError("still waiting for approval")`;

export default function AgentsDocs() {
  return (
    <div className="prose-docs">
      <DocHeader
        title="Agents & approvals"
        lead="Give AI agents and automations their own API keys. They work through the same API, scopes, project restrictions, quotas and audit log as any key, and destructive operations wait for a person to approve them."
      />

      <h2 id="keys">Agent keys</h2>
      <p>
        Create one on the <a href="/agents">Agents</a> page (or tick <em>This key is for an AI agent</em> when creating an API
        key). An agent key is an ordinary API key with two differences: its requests and changes are shown per key on the Agents
        page, and it can never run a destructive operation on its own. Grant it only the scopes it needs and, where possible,
        restrict it to one project.
      </p>
      <p>
        Any other API key can opt in to the same safeguard with <em>Require my approval for destructive actions</em>. Turning
        the safeguard off again asks you to confirm your password.
      </p>

      <h2 id="approval">Operations that need approval</h2>
      <table>
        <thead><tr><th>Operation</th><th>Endpoint</th></tr></thead>
        <tbody>
          <tr><td>Delete a database</td><td><code>DELETE /v1/databases/{"{id}"}</code></td></tr>
          <tr><td>Rotate a database password</td><td><code>POST /v1/databases/{"{id}"}/credentials/rotate</code></td></tr>
          <tr><td>Delete a project</td><td><code>DELETE /v1/projects/{"{id}"}</code></td></tr>
          <tr><td>Delete a backup</td><td><code>DELETE /v1/backups/{"{id}"}</code></td></tr>
        </tbody>
      </table>
      <p>
        The request is validated first (scopes, confirmation name, current state). If it is valid, nothing is changed; the API
        answers <code>202</code> with <code>status: pending_approval</code>, and you get an email and a notification. Repeating
        the same request while it is pending returns the same approval instead of creating a new one.
      </p>
      <CodeBlock code={PENDING} lang="http" title="Response to a destructive call" />

      <h2 id="deciding">Approving or rejecting</h2>
      <p>
        Open <a href="/approvals">Approvals</a> in the dashboard. Approving asks for your password if you haven&apos;t signed in
        during the last 15 minutes, then runs the operation immediately <em>as you</em>, with the same checks as if you had done
        it yourself. If it can no longer run (for example, the project isn&apos;t empty any more), the request is marked
        <em> failed</em> with the reason, and nothing is changed. Requests expire after 24 hours.
      </p>
      <Callout tone="warning" title="API keys can't approve">
        Approvals can only be decided from a signed-in dashboard session. No API key, including a full-access key, can approve
        its own or any other request.
      </Callout>

      <h2 id="polling">Waiting for the decision</h2>
      <p>
        An agent can check its own requests with <code>GET /v1/approvals/{"{id}"}</code> or list them with
        <code> GET /v1/approvals</code>. A key only sees requests it made. The final status is <code>executed</code>,
        <code> failed</code>, <code>rejected</code> (with your note in <code>decision_reason</code>) or <code>expired</code>.
      </p>
      <CodeBlock code={POLL} lang="python" title="Polling from an agent" />

      <h2 id="activity">Activity</h2>
      <p>
        The Agents page shows, per key, requests in the last 24 hours and 7 days (counted per hour, updated every minute), the
        changes it made, failures, and pending approvals. The <a href="/activity">activity log</a> can be filtered by API key.
        Read-only requests are counted but not individually logged.
      </p>
    </div>
  );
}
