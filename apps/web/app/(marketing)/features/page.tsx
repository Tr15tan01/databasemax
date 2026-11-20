import {
  Activity, ArchiveRestore, Bell, Boxes, Copy, DatabaseBackup, Gauge, KeyRound, Layers, Lock, PauseCircle, Plug, RefreshCcw, ScrollText, Server, ShieldCheck, Terminal, Trash2, Users, Zap,
} from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHero } from "@/features/marketing/page-hero";
import { FeatureCard, Section } from "@/features/marketing/section";

export const metadata: Metadata = { title: "Features" };

const ROADMAP = [
  ["Point-in-time recovery", "Not planned yet"],
  ["Multiple hosts and regions", "Phase 5"],
  ["Team collaboration and roles", "Phase 6"],
];

export default function FeaturesPage() {
  return (
    <>
      <PageHero
        eyebrow="Features"
        title="Managed PostgreSQL, without the ceremony."
        description="DatabaseMax provisions and manages PostgreSQL databases. Here's everything that works today — and what's coming next."
      />
      <Section title="Provisioning" description="Asynchronous, observable and safe to retry.">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <FeatureCard icon={Zap} title="Seconds, not tickets">Create a database from the dashboard or API and watch each stage — validation, instance creation, configuration and a live connectivity test.</FeatureCard>
          <FeatureCard icon={RefreshCcw} title="Idempotent requests">Send an Idempotency-Key and retries never create duplicates. Failed attempts are retried automatically with backoff.</FeatureCard>
          <FeatureCard icon={Boxes} title="Clean failure handling">If provisioning ultimately fails, every resource created for it is removed. You can retry with one click.</FeatureCard>
        </div>
      </Section>
      <Section title="Isolation and connectivity">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <FeatureCard icon={Server} title="Dedicated instance">Each database is its own PostgreSQL cluster on its own fixed-size volume, with CPU, memory and process limits.</FeatureCard>
          <FeatureCard icon={Lock} title="TLS required">Our gateway terminates TLS 1.2+ and refuses plain-text connections. Use sslmode=verify-full for certificate verification.</FeatureCard>
          <FeatureCard icon={Plug} title="Connection limits">Per-database connection caps from your plan, enforced both by the gateway and by PostgreSQL.</FeatureCard>
        </div>
      </Section>
      <Section title="Backups and copies" description="Logical backups you can actually restore — and a record of the tests that prove it.">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <FeatureCard icon={DatabaseBackup} title="Daily encrypted backups">Every running database is backed up daily with pg_dump and encrypted (AES-256-GCM) as it streams out. Retention follows your plan.</FeatureCard>
          <FeatureCard icon={ArchiveRestore} title="Restore to a new database">Restore any backup into a fresh database with its own URL and credentials. Nothing is ever overwritten in place.</FeatureCard>
          <FeatureCard icon={Copy} title="Copies for staging">Clone the current data of a database into a new one — ideal for staging, migrations rehearsals and debugging.</FeatureCard>
          <FeatureCard icon={ShieldCheck} title="Tested restores">Backups are regularly restored into isolated, unreachable instances and checked, then destroyed.</FeatureCard>
          <FeatureCard icon={Trash2} title="Final backup on delete">Keep a last backup when you delete a database, restorable for the rest of the retention period.</FeatureCard>
          <FeatureCard icon={Lock} title="Tamper-evident">Every chunk of a backup is authenticated; a modified or truncated backup is rejected before anything is restored.</FeatureCard>
        </div>
      </Section>
      <Section title="Day-two operations">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <FeatureCard icon={KeyRound} title="Credential rotation">A new password in one step; the old one stops working and open sessions are closed.</FeatureCard>
          <FeatureCard icon={PauseCircle} title="Stop and start">Stop databases you don&apos;t need; data stays on the volume.</FeatureCard>
          <FeatureCard icon={Layers} title="Projects & environments">Organise databases into projects labeled development, staging or production.</FeatureCard>
          <FeatureCard icon={Gauge} title="Usage and quotas">Measured storage and connections, plan limits and threshold alerts.</FeatureCard>
          <FeatureCard icon={ScrollText} title="Activity log">An audit trail of every sensitive action and who — person or API key — performed it.</FeatureCard>
          <FeatureCard icon={Bell} title="Notifications">In-app and email notifications for provisioning, rotations, usage thresholds and billing.</FeatureCard>
          <FeatureCard icon={Terminal} title="HTTP API">Everything in the dashboard is available through a versioned, documented REST API.</FeatureCard>
          <FeatureCard icon={Activity} title="Lifecycle events">A full history of each database&apos;s state changes.</FeatureCard>
          <FeatureCard icon={Users} title="Scoped API keys">Keys limited to scopes and specific projects — built so future automation never needs blanket access.</FeatureCard>
        </div>
      </Section>
      <Section title="On the roadmap" description="Not available yet. We'll only list these as features once they're built and tested.">
        <div className="divide-y divide-border rounded-xl border border-border bg-surface">
          {ROADMAP.map(([t, p]) => (
            <div key={t} className="flex items-center justify-between gap-4 px-5 py-4 text-sm">
              <span>{t}</span>
              <Badge variant="outline">{p}</Badge>
            </div>
          ))}
        </div>
        <div className="mt-10">
          <Button asChild><Link href="/register">Start building free</Link></Button>
        </div>
      </Section>
    </>
  );
}
