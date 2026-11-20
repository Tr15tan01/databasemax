import { ArrowRight, BookOpen, Code2, Plug } from "lucide-react";
import Link from "next/link";

import { Callout, DocHeader } from "@/features/docs/doc-page";

export const metadata = { title: "Introduction" };

export default function DocsHome() {
  return (
    <div className="prose-docs">
      <DocHeader title="DatabaseMax documentation" lead="DatabaseMax provisions and manages PostgreSQL databases. Create one in the dashboard or with the HTTP API, copy the connection URL, and connect with any PostgreSQL client." />
      <div className="not-prose grid gap-3 sm:grid-cols-3">
        {[
          { href: "/docs/getting-started", icon: BookOpen, t: "Getting started", d: "From sign-up to your first query." },
          { href: "/docs/connection", icon: Plug, t: "Connecting", d: "Connection URLs, TLS and drivers." },
          { href: "/docs/api", icon: Code2, t: "API reference", d: "Every endpoint, with examples." },
        ].map((c) => (
          <Link key={c.href} href={c.href} className="group rounded-xl border border-border bg-surface p-4 !no-underline transition-colors hover:border-border-strong">
            <c.icon className="size-4 text-muted" aria-hidden />
            <div className="mt-3 flex items-center gap-1 text-sm font-semibold text-foreground">
              {c.t} <ArrowRight className="size-3.5 opacity-0 transition-opacity group-hover:opacity-100" />
            </div>
            <div className="mt-1 text-[13px] text-muted">{c.d}</div>
          </Link>
        ))}
      </div>
      <h2>Concepts</h2>
      <ul>
        <li><strong>Project</strong> — a logical container for databases, labeled with an environment (development, staging or production).</li>
        <li><strong>Database</strong> — an isolated PostgreSQL instance with its own storage, credentials and limits. It contains one PostgreSQL database that your role owns.</li>
        <li><strong>Connection URL</strong> — a standard <code>postgresql://</code> URL. TLS is always required.</li>
        <li><strong>API key</strong> — a scoped credential for automation. Keys are shown once and can be revoked at any time.</li>
      </ul>
      <h2>Lifecycle</h2>
      <p>Creating a database is asynchronous. It moves through these states, all visible in the dashboard and API:</p>
      <p><code>pending → validating → queued → provisioning → configuring → testing → ready</code></p>
      <p>A ready database can be <code>stopped</code> and started again. Deleting moves it to <code>deleting</code> and then <code>deleted</code>. If provisioning fails after automatic retries, the database becomes <code>failed</code>, any resources are cleaned up, and you can retry.</p>
      <Callout title="Backups">
        Running databases are backed up daily, encrypted, and kept for your plan&apos;s retention period. Restores always create a new database. See <a href="/docs/backups">Backups &amp; restore</a>.
      </Callout>
    </div>
  );
}
