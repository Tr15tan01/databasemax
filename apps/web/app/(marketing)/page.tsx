import {
  Activity,
  ArrowRight,
  BarChart3,
  Clock,
  KeyRound,
  Lock,
  Network,
  PauseCircle,
  RefreshCcw,
  ScrollText,
  Server,
  Shield,
  ShieldCheck,
  Terminal,
  Zap,
} from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { CodeBlock } from "@/components/ui/code-block";
import { DashboardPreview } from "@/features/marketing/dashboard-preview";
import { PricingCards } from "@/features/marketing/pricing-cards";
import { FeatureCard, Section } from "@/features/marketing/section";
import { getPlans } from "@/lib/server/api";

export const dynamic = "force-dynamic";

const FAQ = [
  {
    q: "What exactly does DatabaseMax provide?",
    a: "Managed PostgreSQL databases. Each database runs in its own isolated PostgreSQL instance with its own credentials and resource limits, reachable over TLS with a standard connection URL. DatabaseMax is not an application backend — there are no auth, storage or function products.",
  },
  {
    q: "Which PostgreSQL versions are available?",
    a: "The versions offered are listed when you create a database. At launch that is PostgreSQL 16; new major versions are added after they're tested on our platform.",
  },
  {
    q: "Do I need a CLI, SDK or GitHub account?",
    a: "No. Everything is available in the dashboard and through the HTTP API. Any PostgreSQL client or driver works with the connection URL.",
  },
  {
    q: "Are backups included?",
    a: "Yes. Every running database gets a daily logical backup, encrypted before it's stored and kept for 3, 7 or 14 days depending on your plan. You can take on-demand backups, keep a final backup when deleting, and restore any backup into a new database. Backups are regularly test-restored in isolated instances. Point-in-time recovery is not offered: you restore to the moment a backup was taken.",
  },
  {
    q: "Is it highly available?",
    a: "No. The initial platform runs on a single, carefully partitioned host. We don't claim automatic failover or replication until they exist and have been tested.",
  },
  {
    q: "What happens if a payment fails?",
    a: "Your plan stays active during a 14-day grace period while you update your payment method. Databases are never deleted because a payment failed; after the grace period, resources above the Free plan limits are stopped, not deleted.",
  },
];

export default async function LandingPage() {
  const plans = await getPlans();
  return (
    <>
      {/* 1. Hero */}
      <section className="relative overflow-hidden">
        <div aria-hidden className="hairline-grid pointer-events-none absolute inset-0 -z-10 opacity-60" />
        <div className="mx-auto max-w-6xl px-5 pb-16 pt-20 md:pt-28">
          <div className="mx-auto max-w-3xl text-center">
            <Link
              href="/docs/getting-started"
              className="mb-6 inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1 text-xs text-muted shadow-xs transition-colors hover:text-foreground"
            >
              <span className="size-1.5 rounded-full bg-good" aria-hidden />
              Managed PostgreSQL 16 · TLS by default
              <ArrowRight className="size-3" aria-hidden />
            </Link>
            <h1 className="text-balance text-4xl font-semibold tracking-[-0.035em] sm:text-5xl md:text-[64px] md:leading-[1.02]">
              PostgreSQL, ready when you are.
            </h1>
            <p className="mx-auto mt-6 max-w-xl text-balance text-[17px] leading-relaxed text-muted">
              Create a database, get your connection URL, and focus on building your product instead of managing infrastructure.
            </p>
            <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Button size="lg" asChild>
                <Link href="/register">
                  Start building free <ArrowRight />
                </Link>
              </Button>
              <Button size="lg" variant="secondary" asChild>
                <Link href="#features">Explore features</Link>
              </Button>
            </div>
            <p className="mt-4 text-xs text-subtle">Free plan · No credit card required</p>
          </div>
          <div className="mx-auto mt-16 max-w-5xl">
            <DashboardPreview />
          </div>
        </div>
      </section>

      {/* 2. Benefits */}
      <Section
        id="features"
        eyebrow="Why DatabaseMax"
        title="The database part of your stack, handled."
        description="Real PostgreSQL with sensible defaults. No proprietary query layer, no lock-in — just a connection URL your existing tools understand."
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <FeatureCard icon={Zap} title="Ready in seconds">
            Provisioning runs asynchronously and typically finishes in under ten seconds. Watch each stage as it happens.
          </FeatureCard>
          <FeatureCard icon={Server} title="Isolated by design">
            Every database is its own PostgreSQL instance with dedicated storage, credentials and CPU/memory limits.
          </FeatureCard>
          <FeatureCard icon={Lock} title="Encrypted connections">
            TLS is required on every connection. Plain-text connections are refused before they reach your database.
          </FeatureCard>
          <FeatureCard icon={Terminal} title="No CLI required">
            Everything works from the dashboard and the HTTP API. Use any PostgreSQL driver you already know.
          </FeatureCard>
        </div>
      </Section>

      {/* 3. How it works */}
      <section className="border-y border-border bg-surface/50">
        <div className="mx-auto grid max-w-6xl gap-12 px-5 py-20 md:grid-cols-2 md:py-24">
          <div>
            <p className="mb-3 text-[13px] font-medium text-accent">How it works</p>
            <h2 className="text-3xl font-semibold tracking-[-0.025em]">Three steps to a working database.</h2>
            <ol className="mt-10 space-y-8">
              {[
                ["Create a project", "Group databases by app and environment — development, staging or production."],
                ["Create a database", "Pick a name, a PostgreSQL version and a size. We provision an isolated instance and verify it end to end."],
                ["Connect", "Copy the connection URL into your app's environment. TLS and routing are already set up."],
              ].map(([t, d], i) => (
                <li key={t} className="flex gap-4">
                  <span className="grid size-8 shrink-0 place-items-center rounded-full border border-border bg-surface font-mono text-xs">
                    {i + 1}
                  </span>
                  <div>
                    <h3 className="text-sm font-semibold">{t}</h3>
                    <p className="mt-1 text-[13px] leading-relaxed text-muted">{d}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
          <div className="flex flex-col justify-center gap-4">
            <CodeBlock
              title=".env"
              lang="dotenv"
              wrap
              code={"DATABASE_URL=postgresql://u_k3j9x2ab1c:<password>@db-k3j9x2ab1c.db.databasemax.dev:5432/app?sslmode=require"}
            />
            <CodeBlock
              title="app.ts"
              lang="typescript"
              code={`import pg from "pg";

const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });
const { rows } = await pool.query("select now()");`}
            />
          </div>
        </div>
      </section>

      {/* 4. Management features */}
      <Section
        eyebrow="Database management"
        title="Everything you need to run a database — nothing you don't."
        description="Day-two operations are a click (or an API call) away, with clear confirmations before anything destructive."
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <FeatureCard icon={RefreshCcw} title="Credential rotation">
            Rotate a password in one step. The old password stops working immediately and open sessions are closed.
          </FeatureCard>
          <FeatureCard icon={PauseCircle} title="Stop and start">
            Pause a database you don&apos;t need right now. Your data stays on its volume until you start it again.
          </FeatureCard>
          <FeatureCard icon={KeyRound} title="Scoped API keys">
            Automate with API keys limited to specific scopes and projects. Revoke any key instantly.
          </FeatureCard>
          <FeatureCard icon={ScrollText} title="Activity log">
            Every sensitive change — creation, rotation, deletion, key usage — is recorded with who did it and when.
          </FeatureCard>
          <FeatureCard icon={Clock} title="Lifecycle events">
            Follow provisioning stage by stage, with clear errors and one-click retry if something fails.
          </FeatureCard>
          <FeatureCard icon={Network} title="Standard connection URLs">
            A normal <code className="font-mono text-[12px]">postgresql://</code> URL with correct encoding and TLS settings.
          </FeatureCard>
        </div>
      </Section>

      {/* 5. Usage monitoring */}
      <section className="border-y border-border bg-surface/50">
        <div className="mx-auto grid max-w-6xl items-center gap-12 px-5 py-20 md:grid-cols-[1fr_1.1fr] md:py-24">
          <div>
            <p className="mb-3 text-[13px] font-medium text-accent">Usage monitoring</p>
            <h2 className="text-3xl font-semibold tracking-[-0.025em]">Numbers you can trust.</h2>
            <p className="mt-4 text-[15px] leading-relaxed text-muted">
              Storage and connections are measured directly from PostgreSQL every few minutes. Memory and CPU come from the
              instance&apos;s own resource accounting. Allocations and estimates are always labeled as such — and nothing is invented.
            </p>
            <ul className="mt-6 space-y-3 text-[13px] text-muted">
              {["Storage used vs. allocated, per database and per project", "Active connections against your plan's limit", "API request volume per billing period", "Alerts at 70%, 85% and 100% of plan limits"].map((t) => (
                <li key={t} className="flex gap-2.5">
                  <BarChart3 className="mt-0.5 size-4 shrink-0 text-foreground" aria-hidden /> {t}
                </li>
              ))}
            </ul>
          </div>
          <div aria-hidden className="rounded-xl border border-border bg-surface p-5 shadow-[var(--shadow-soft)]">
            {[
              ["Storage", "312 MB of 500 MB", 62],
              ["Connections", "8 of 20", 40],
              ["Databases", "1 of 1", 100],
            ].map(([label, value, p]) => (
              <div key={label as string} className="border-b border-border py-4 first:pt-0 last:border-0 last:pb-0">
                <div className="flex justify-between text-[13px]">
                  <span className="font-medium">{label}</span>
                  <span className="tabular text-muted">{value}</span>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3">
                  <div className={`h-full rounded-full ${Number(p) >= 100 ? "bg-critical" : "bg-accent"}`} style={{ width: `${p}%` }} />
                </div>
              </div>
            ))}
            <p className="mt-4 flex items-center gap-1.5 text-[11px] text-subtle">
              <Activity className="size-3" /> Illustration · measured every 5 minutes
            </p>
          </div>
        </div>
      </section>

      {/* 6. Security */}
      <Section
        eyebrow="Security and isolation"
        title="Secure defaults, documented honestly."
        description="We publish how isolation works and what is still on our roadmap."
      >
        <div className="grid gap-4 md:grid-cols-3">
          <FeatureCard icon={ShieldCheck} title="One instance per database">
            No shared clusters. Each database is its own PostgreSQL server with its own system user, storage and limits, and no Linux capabilities.
          </FeatureCard>
          <FeatureCard icon={Shield} title="Least privilege everywhere">
            Your role owns its database but can&apos;t become a superuser. Infrastructure control is isolated in a private service.
          </FeatureCard>
          <FeatureCard icon={Lock} title="Secrets handled carefully">
            Passwords are encrypted at rest, sent to PostgreSQL only as SCRAM verifiers and never written to logs.
          </FeatureCard>
        </div>
        <div className="mt-8">
          <Button variant="secondary" asChild>
            <Link href="/security">
              Read the security overview <ArrowRight />
            </Link>
          </Button>
        </div>
      </Section>

      {/* 7. Pricing */}
      <section className="border-y border-border bg-surface/50">
        <div className="mx-auto max-w-6xl px-5 py-20 md:py-24">
          <div className="mx-auto mb-12 max-w-xl text-center">
            <p className="mb-3 text-[13px] font-medium text-accent">Pricing</p>
            <h2 className="text-3xl font-semibold tracking-[-0.025em]">Simple plans with clear limits.</h2>
            <p className="mt-4 text-[15px] text-muted">Flat monthly prices in USD. Every limit is enforced and visible in your dashboard.</p>
          </div>
          <PricingCards plans={plans} compact />
          <p className="mt-6 text-center text-[13px]">
            <Link href="/pricing" className="text-accent hover:underline">
              Compare plans in detail →
            </Link>
          </p>
        </div>
      </section>

      {/* 8. Docs preview */}
      <Section
        eyebrow="Developer documentation"
        title="Works with the tools you already use."
        description="Clear guides for connecting from any language, plus a complete HTTP API reference generated from the live service."
      >
        <div className="grid gap-4 lg:grid-cols-3">
          <CodeBlock title="psql" lang="bash" wrap code={`psql "postgresql://u_k3j9x2ab1c:<password>@db-k3j9x2ab1c.db.databasemax.dev:5432/app?sslmode=require"`} />
          <CodeBlock
            title="Python (psycopg)"
            lang="python"
            code={`import os, psycopg

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    print(conn.execute("select version()").fetchone())`}
          />
          <CodeBlock
            title="Create a database via API"
            lang="bash"
            code={`curl -X POST https://databasemax.dev/api/v1/databases \\
  -H "Authorization: Bearer $DMX_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"project_id": "…", "name": "orders"}'`}
          />
        </div>
        <div className="mt-8 flex flex-wrap gap-3">
          <Button variant="secondary" asChild>
            <Link href="/docs/getting-started">Getting started</Link>
          </Button>
          <Button variant="ghost" asChild>
            <Link href="/docs/api">API reference →</Link>
          </Button>
        </div>
      </Section>

      {/* 9. FAQ */}
      <section className="border-t border-border">
        <div className="mx-auto grid max-w-6xl gap-10 px-5 py-20 md:grid-cols-[1fr_1.6fr] md:py-24">
          <div>
            <h2 className="text-3xl font-semibold tracking-[-0.025em]">Frequently asked questions</h2>
            <p className="mt-4 text-[15px] text-muted">
              Something else? <Link href="/contact" className="text-accent hover:underline">Get in touch</Link>.
            </p>
          </div>
          <div className="divide-y divide-border border-y border-border">
            {FAQ.map((f) => (
              <details key={f.q} className="group py-4">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-[15px] font-medium [&::-webkit-details-marker]:hidden">
                  {f.q}
                  <span className="text-subtle transition-transform group-open:rotate-45" aria-hidden>
                    +
                  </span>
                </summary>
                <p className="mt-3 text-[14px] leading-relaxed text-muted">{f.a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* 10. Final CTA */}
      <section className="px-5 pb-24">
        <div className="relative mx-auto max-w-6xl overflow-hidden rounded-2xl bg-primary px-8 py-16 text-center text-primary-foreground md:py-20">
          <h2 className="text-balance text-3xl font-semibold tracking-[-0.03em] md:text-4xl">Your next database is one click away.</h2>
          <p className="mx-auto mt-4 max-w-md text-[15px] opacity-70">Start on the Free plan. Upgrade when your project needs more room.</p>
          <div className="mt-8 flex justify-center gap-3">
            <Button size="lg" className="bg-primary-foreground text-primary hover:bg-primary-foreground/90" asChild>
              <Link href="/register">
                Start building free <ArrowRight />
              </Link>
            </Button>
          </div>
        </div>
      </section>
    </>
  );
}
