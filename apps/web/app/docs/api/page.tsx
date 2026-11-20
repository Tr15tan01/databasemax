import { CodeBlock } from "@/components/ui/code-block";
import { Callout, DocHeader } from "@/features/docs/doc-page";
import {
  API_VERSION,
  type Operation,
  opId,
  operationsByTag,
  refName,
  resolve,
  TAG_TITLES,
  typeLabel,
} from "@/features/docs/openapi";

export const metadata = { title: "API reference" };

const METHOD_COLORS: Record<string, string> = {
  GET: "text-accent bg-accent-soft",
  POST: "text-good bg-good-soft",
  PATCH: "text-warning bg-warning-soft",
  PUT: "text-warning bg-warning-soft",
  DELETE: "text-critical bg-critical-soft",
};

function curlFor(op: Operation): string {
  const path = op.path.replace(/\{(\w+)\}/g, (_, n: string) => `<${n}>`);
  const lines = [`curl -X ${op.method} "https://databasemax.dev${path}"`, `  -H "Authorization: Bearer $DMX_API_KEY"`];
  const body = resolve(op.requestBody?.content?.["application/json"]?.schema);
  if (body?.properties) {
    const example: Record<string, unknown> = {};
    for (const name of body.required ?? []) example[name] = `<${name}>`;
    lines.push(`  -H "Content-Type: application/json"`, `  -d '${JSON.stringify(example)}'`);
  }
  return lines.join(" \\\n");
}

function Endpoint({ op }: { op: Operation }) {
  const params = (op.parameters ?? []).filter((p) => p.in !== "header" || p.name === "Idempotency-Key");
  const body = op.requestBody?.content?.["application/json"]?.schema;
  const bodySchema = resolve(body);
  const okCode = Object.keys(op.responses ?? {}).find((c) => c.startsWith("2"));
  const okSchema = okCode ? op.responses?.[okCode]?.content?.["application/json"]?.schema : undefined;
  return (
    <section id={opId(op)} className="scroll-mt-24 border-t border-border py-8 first:border-0">
      <div className="flex flex-wrap items-center gap-2.5">
        <span className={`rounded-md px-2 py-0.5 font-mono text-[11px] font-semibold ${METHOD_COLORS[op.method] ?? ""}`}>{op.method}</span>
        <code className="!border-0 !bg-transparent !p-0 font-mono text-[13px] !text-foreground">{op.path}</code>
      </div>
      <h3 className="!mt-3">{op.summary}</h3>
      {params.length > 0 && (
        <table>
          <thead><tr><th>Parameter</th><th>In</th><th>Type</th><th>Required</th></tr></thead>
          <tbody>
            {params.map((p) => (
              <tr key={`${p.in}-${p.name}`}>
                <td><code>{p.name}</code>{p.description && <div className="mt-1 text-xs">{p.description}</div>}</td>
                <td>{p.in}</td>
                <td className="font-mono text-xs">{typeLabel(p.schema)}</td>
                <td>{p.required ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {bodySchema?.properties && (
        <>
          <p className="!mb-1 text-xs font-semibold uppercase tracking-wide !text-subtle">Request body · {refName(body)}</p>
          <table>
            <thead><tr><th>Field</th><th>Type</th><th>Required</th></tr></thead>
            <tbody>
              {Object.entries(bodySchema.properties).map(([name, prop]) => (
                <tr key={name}>
                  <td><code>{name}</code>{prop.description && <div className="mt-1 text-xs">{prop.description}</div>}</td>
                  <td className="font-mono text-xs">{typeLabel(prop)}</td>
                  <td>{bodySchema.required?.includes(name) ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <p className="text-[13px]">
        Returns <strong>{okCode ?? "200"}</strong>
        {refName(okSchema) && <> with <code>{refName(okSchema)}</code></>}.
      </p>
      <CodeBlock lang="bash" code={curlFor(op)} />
    </section>
  );
}

export default function ApiReference() {
  const groups = operationsByTag();
  return (
    <div className="prose-docs">
      <DocHeader
        title="API reference"
        lead={`Version ${API_VERSION}. All endpoints live under /api/v1 and are generated from the running service's OpenAPI schema, so this page always matches the implementation.`}
      />
      <nav aria-label="API sections" className="not-prose mb-10 flex flex-wrap gap-2">
        {groups.map((g) => (
          <a key={g.tag} href={`#tag-${g.tag}`} className="rounded-full border border-border bg-surface px-3 py-1 text-[13px] !text-muted !no-underline hover:!text-foreground">
            {TAG_TITLES[g.tag] ?? g.tag}
          </a>
        ))}
      </nav>

      <h2 id="authentication">Authentication</h2>
      <p>
        Create an API key under <strong>API keys</strong> in the dashboard and send it as a bearer token. Keys are shown once, stored
        only as a hash, and carry explicit scopes; a key can optionally be limited to specific projects. API keys can&apos;t create
        other API keys or change account settings.
      </p>
      <CodeBlock lang="bash" code={`curl https://databasemax.dev/api/v1/databases -H "Authorization: Bearer dmx_live_xxxxxxxx_…"`} />
      <table>
        <thead><tr><th>Scope</th><th>Allows</th></tr></thead>
        <tbody>
          {[
            ["projects:read", "List and read projects"],
            ["projects:write", "Create, update and archive projects"],
            ["projects:delete", "Delete (empty) projects"],
            ["databases:read", "List and read databases, status and events"],
            ["databases:write", "Create, rename, stop and start databases"],
            ["databases:delete", "Delete databases"],
            ["credentials:read", "Read connection details including the password"],
            ["credentials:rotate", "Rotate database credentials"],
            ["usage:read", "Read usage, quotas and billing estimates"],
            ["activity:read", "Read the activity log"],
          ].map(([s, d]) => (
            <tr key={s}><td><code>{s}</code></td><td>{d}</td></tr>
          ))}
        </tbody>
      </table>

      <h2 id="errors">Errors and request IDs</h2>
      <p>Errors share one shape. Include the <code>request_id</code> (also in the <code>X-Request-ID</code> header) when contacting support.</p>
      <CodeBlock lang="json" code={`{
  "error": {
    "code": "quota_exceeded",
    "message": "Your Free plan allows 1 database(s). Upgrade to create more.",
    "request_id": "8f1c2b7e6d0a4b5c9e3f1a2b3c4d5e6f",
    "details": { "limit": 1, "used": 1, "resource": "databases" }
  }
}`} />
      <p>Rate-limited requests return <code>429</code> with a <code>Retry-After</code> header.</p>

      <h2 id="idempotency">Idempotency</h2>
      <p>
        <code>POST /projects</code> and <code>POST /databases</code> accept an <code>Idempotency-Key</code> header (8–128 characters).
        Retrying with the same key and body returns the original response (with <code>Idempotent-Replayed: true</code>) instead of
        creating a duplicate. Reusing a key with a different body returns <code>422</code>. Keys expire after 24 hours.
      </p>

      <h2 id="examples">Complete examples</h2>
      <CodeBlock
        title="Node.js (fetch)"
        lang="javascript"
        code={`const API = "https://databasemax.dev/api/v1";
const headers = { Authorization: \`Bearer \${process.env.DMX_API_KEY}\`, "Content-Type": "application/json" };

const db = await fetch(\`\${API}/databases\`, {
  method: "POST",
  headers: { ...headers, "Idempotency-Key": crypto.randomUUID() },
  body: JSON.stringify({ project_id: process.env.DMX_PROJECT_ID, name: "orders" }),
}).then((r) => r.json());

let status = db.status;
while (status !== "ready" && status !== "failed") {
  await new Promise((r) => setTimeout(r, 2000));
  status = (await fetch(\`\${API}/databases/\${db.id}\`, { headers }).then((r) => r.json())).status;
}
const { url } = await fetch(\`\${API}/databases/\${db.id}/connection?reveal=true\`, { headers }).then((r) => r.json());`}
      />
      <div className="h-4" />
      <CodeBlock
        title="Python (httpx)"
        lang="python"
        code={`import os, time, uuid, httpx

api = httpx.Client(base_url="https://databasemax.dev/api/v1",
                   headers={"Authorization": f"Bearer {os.environ['DMX_API_KEY']}"})

db = api.post("/databases", json={"project_id": os.environ["DMX_PROJECT_ID"], "name": "orders"},
              headers={"Idempotency-Key": str(uuid.uuid4())}).raise_for_status().json()
while (status := api.get(f"/databases/{db['id']}").json()["status"]) not in ("ready", "failed"):
    time.sleep(2)
url = api.get(f"/databases/{db['id']}/connection", params={"reveal": "true"}).json()["url"]`}
      />
      <Callout title="Browser sessions">
        The dashboard uses the same API with an HttpOnly session cookie. Cookie-authenticated state-changing requests must include
        the <code>X-CSRF-Token</code> header; API-key requests don&apos;t need it.
      </Callout>

      {groups.map((g) => (
        <div key={g.tag}>
          <h2 id={`tag-${g.tag}`}>{TAG_TITLES[g.tag] ?? g.tag}</h2>
          {g.ops.map((op) => (
            <Endpoint key={`${op.method}-${op.path}`} op={op} />
          ))}
        </div>
      ))}
    </div>
  );
}
