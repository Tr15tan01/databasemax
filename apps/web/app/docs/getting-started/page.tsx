import { CodeBlock } from "@/components/ui/code-block";
import { Callout, DocHeader } from "@/features/docs/doc-page";

export const metadata = { title: "Getting started" };

export default function GettingStarted() {
  return (
    <div className="prose-docs">
      <DocHeader title="Getting started" lead="Create your first database and run a query in about five minutes. No CLI or SDK needed." />
      <h2 id="account">1. Create an account</h2>
      <p>Register with your email and a password (or with Google, if enabled). We send a verification link — verifying your email is required before you can create databases.</p>
      <h2 id="project">2. Create a project</h2>
      <p>After signing in, onboarding asks for a project name and environment. Projects group related databases; the environment label helps you tell production from development at a glance.</p>
      <h2 id="database">3. Create a database</h2>
      <p>Choose a name, PostgreSQL version and size. The options you see reflect your plan. Provisioning usually takes a few seconds; you can watch each stage on the provisioning screen.</p>
      <p>We only mark a database <strong>ready</strong> after a real connection through our TLS gateway, using your credentials, succeeds.</p>
      <h2 id="connect">4. Connect</h2>
      <p>Open the database&apos;s <strong>Connection</strong> tab and copy the URL. Revealing the password asks you to confirm it&apos;s you if you haven&apos;t signed in recently.</p>
      <CodeBlock lang="bash" code={`export DATABASE_URL='postgresql://u_k3j9x2ab1c:<password>@db-k3j9x2ab1c.db.databasemax.dev:5432/app?sslmode=require'
psql "$DATABASE_URL" -c 'select version();'`} />
      <Callout title="Keep the URL secret">
        The URL contains your password. Store it in your hosting provider&apos;s secret manager or an untracked <code>.env</code> file — never commit it.
      </Callout>
      <h2 id="api">Doing the same with the API</h2>
      <p>Create an API key in <strong>API keys</strong> with the scopes <code>projects:write</code>, <code>databases:write</code>, <code>databases:read</code> and <code>credentials:read</code>, then:</p>
      <CodeBlock lang="bash" code={`API=https://databasemax.dev/api/v1
AUTH="Authorization: Bearer $DMX_API_KEY"

# 1. create a project
PROJECT=$(curl -s -X POST $API/projects -H "$AUTH" -H 'Content-Type: application/json' \\
  -d '{"name": "my-app", "environment": "development"}' | jq -r .id)

# 2. create a database (returns immediately with status "queued")
DB=$(curl -s -X POST $API/databases -H "$AUTH" -H 'Content-Type: application/json' \\
  -H "Idempotency-Key: create-my-app-db-1" \\
  -d "{\\"project_id\\": \\"$PROJECT\\", \\"name\\": \\"main\\"}" | jq -r .id)

# 3. wait until ready
until [ "$(curl -s $API/databases/$DB -H "$AUTH" | jq -r .status)" = ready ]; do sleep 2; done

# 4. get the connection URL
curl -s "$API/databases/$DB/connection?reveal=true" -H "$AUTH" | jq -r .url`} />
    </div>
  );
}
