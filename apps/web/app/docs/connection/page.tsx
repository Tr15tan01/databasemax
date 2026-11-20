import { CodeBlock } from "@/components/ui/code-block";
import { Callout, DocHeader } from "@/features/docs/doc-page";

export const metadata = { title: "Connecting to your database" };

const URL_EXAMPLE = "postgresql://u_k3j9x2ab1c:<password>@db-k3j9x2ab1c.db.databasemax.dev:5432/app?sslmode=require";

export default function ConnectionDocs() {
  return (
    <div className="prose-docs">
      <DocHeader title="Connecting to your database" lead="Every database has a standard PostgreSQL connection URL. TLS is required on every connection." />
      <h2 id="url">Anatomy of the connection URL</h2>
      <CodeBlock lang="text" wrap code={URL_EXAMPLE} />
      <table>
        <thead><tr><th>Part</th><th>Example</th><th>Notes</th></tr></thead>
        <tbody>
          <tr><td>Username</td><td><code>u_k3j9x2ab1c</code></td><td>Unique across DatabaseMax; it also identifies your database to our gateway.</td></tr>
          <tr><td>Password</td><td><code>&lt;password&gt;</code></td><td>32 random letters and digits. Percent-encode it if you ever set your own tooling to build URLs.</td></tr>
          <tr><td>Host</td><td><code>db-k3j9x2ab1c.db.databasemax.dev</code></td><td>Your database&apos;s own hostname. It resolves to our TLS gateway.</td></tr>
          <tr><td>Port</td><td><code>5432</code></td><td>The standard PostgreSQL port.</td></tr>
          <tr><td>Database</td><td><code>app</code></td><td>The PostgreSQL database your role owns.</td></tr>
          <tr><td>sslmode</td><td><code>require</code></td><td>Required. Plain-text connections are rejected.</td></tr>
        </tbody>
      </table>
      <h3 id="tls">TLS and certificate verification</h3>
      <p>
        <code>sslmode=require</code> encrypts the connection. To also verify the server&apos;s identity, use
        <code>sslmode=verify-full</code>; the gateway&apos;s certificate is issued by a public certificate authority, so most
        systems verify it with the operating system&apos;s CA bundle (set <code>sslrootcert=system</code> with libpq 16+).
      </p>
      <p>
        Your database&apos;s hostname is sent during the TLS handshake (SNI). If a client can&apos;t send SNI (for example when
        connecting by IP address), routing still works through your unique username.
      </p>
      <Callout title="Channel binding">
        Authentication uses SCRAM-SHA-256 end to end. Because TLS terminates at our gateway, <code>channel_binding=require</code>
        is not supported; the default (<code>prefer</code>) works.
      </Callout>

      <h2 id="drivers">Drivers and frameworks</h2>
      <h3>Node.js (pg)</h3>
      <CodeBlock lang="javascript" code={`import pg from "pg";

const pool = new pg.Pool({
  connectionString: process.env.DATABASE_URL, // includes ?sslmode=require
  max: 10,                                    // stay under your plan's connection limit
});
const { rows } = await pool.query("select now()");`} />
      <h3>Prisma</h3>
      <CodeBlock lang="bash" code={`# .env
DATABASE_URL="${URL_EXAMPLE}"`} />
      <h3>Drizzle (postgres-js)</h3>
      <CodeBlock lang="typescript" code={`import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";

const client = postgres(process.env.DATABASE_URL!, { ssl: "require", max: 10 });
export const db = drizzle(client);`} />
      <h3>Python (psycopg 3)</h3>
      <CodeBlock lang="python" code={`import os
import psycopg

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    row = conn.execute("select current_database(), version()").fetchone()
    print(row)`} />
      <h3>SQLAlchemy</h3>
      <CodeBlock lang="python" code={`from sqlalchemy import create_engine

url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(url, pool_size=5, max_overflow=5, pool_pre_ping=True)`} />
      <h3>Django</h3>
      <CodeBlock lang="python" code={`# settings.py (pip install dj-database-url)
import dj_database_url
DATABASES = {"default": dj_database_url.config(conn_max_age=600, ssl_require=True)}`} />
      <h3>Go (pgx)</h3>
      <CodeBlock lang="go" code={`pool, err := pgxpool.New(ctx, os.Getenv("DATABASE_URL"))
if err != nil { log.Fatal(err) }
defer pool.Close()`} />
      <h3>Ruby on Rails</h3>
      <CodeBlock lang="yaml" code={`# config/database.yml
production:
  url: <%= ENV["DATABASE_URL"] %>
  pool: 5`} />

      <h2 id="limits">Connection limits</h2>
      <p>
        Each plan allows a number of concurrent connections per database (Free: 20, Developer: 50, Pro: 100). When the limit is
        reached, new connections receive error <code>53300 too many connections</code>. Use a connection pool in your application
        and keep its maximum size below your limit — especially with serverless platforms that open many short-lived connections.
      </p>

      <h2 id="troubleshooting">Troubleshooting</h2>
      <table>
        <thead><tr><th>Error</th><th>What it means</th></tr></thead>
        <tbody>
          <tr><td><code>SSL/TLS is required</code></td><td>Add <code>sslmode=require</code> to the URL or enable SSL in your driver.</td></tr>
          <tr><td><code>unknown endpoint or user</code></td><td>The hostname and username don&apos;t belong to the same database, or the database doesn&apos;t exist. Copy the URL again from the dashboard.</td></tr>
          <tr><td><code>password authentication failed</code></td><td>The password is wrong or was rotated. Reveal the current password in the dashboard.</td></tr>
          <tr><td><code>database is stopped</code></td><td>Start the database from its Settings tab.</td></tr>
          <tr><td><code>too many connections</code></td><td>You reached the plan&apos;s connection limit. Reduce pool sizes or upgrade.</td></tr>
          <tr><td><code>cannot execute … in a read-only transaction</code></td><td>The database exceeded its storage allocation. Free space or increase the allocation in Settings.</td></tr>
        </tbody>
      </table>
    </div>
  );
}
