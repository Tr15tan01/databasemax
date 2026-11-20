"use client";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BookOpen, CreditCard, Database, DatabaseBackup, FolderPlus, HardDrive, Plug, Plus, Rocket, Zap } from "lucide-react";
import Link from "next/link";

import { TimeSeriesChart } from "@/components/charts/time-series";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { QuotaList } from "@/features/app/quota-list";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Stat } from "@/components/ui/stat";
import { api } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { ACTOR_LABELS, actionLabel, eventLabel } from "@/lib/labels";
import type { DashboardSummary, SeriesPoint } from "@/lib/types";
import { fillDays, formatBytes, formatCents, formatDate, formatMB, formatNumber, relativeTime } from "@/lib/utils";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function DashboardPage() {
  const { data: me } = useMe();
  const summary = useQuery({ queryKey: ["dashboard"], queryFn: () => api<DashboardSummary>("/dashboard/summary"), refetchInterval: 30_000 });
  const storage = useQuery({
    queryKey: ["usage-history", "storage_bytes", 30],
    queryFn: () => api<{ points: SeriesPoint[] }>("/usage/history", { query: { metric: "storage_bytes", days: 30, bucket: "day" } }),
  });
  const requests = useQuery({
    queryKey: ["usage-history", "api_requests", 14],
    queryFn: () => api<{ points: SeriesPoint[] }>("/usage/history", { query: { metric: "api_requests", days: 14, bucket: "day" } }),
  });

  if (summary.isError) return <ErrorState error={summary.error} onRetry={() => summary.refetch()} />;
  const s = summary.data;
  const name = me?.display_name?.split(" ")[0];

  return (
    <>
      <PageHeader
        title={`${greeting()}${name ? `, ${name}` : ""}`}
        description="Your databases, usage and account at a glance."
        actions={
          <>
            <Button variant="secondary" asChild><Link href="/projects?new=1"><FolderPlus /> New project</Link></Button>
            <Button asChild><Link href="/databases/new"><Plus /> New database</Link></Button>
          </>
        }
      />
      {s && s.counts.projects === 0 && (
        <Card className="mb-6">
          <EmptyState
            icon={Rocket}
            title="Let's create your first database"
            description="Set up a project and a PostgreSQL database in under a minute."
            action={<Button asChild><Link href="/onboarding">Start setup <ArrowRight /></Link></Button>}
          />
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {!s ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[118px] rounded-lg" />)
        ) : (
          <>
            <Stat label="Databases" icon={Database} value={s.counts.databases} sub={
              <span className="flex flex-wrap gap-x-3">
                <span><span className="text-good">●</span> {s.counts.running} running</span>
                {s.counts.provisioning > 0 && <span><span className="text-accent">●</span> {s.counts.provisioning} provisioning</span>}
                {s.counts.failed > 0 && <span><span className="text-critical">●</span> {s.counts.failed} failed</span>}
                {s.counts.stopped > 0 && <span>{s.counts.stopped} stopped</span>}
              </span>
            } />
            <Stat label="Storage used" icon={HardDrive} value={formatBytes(s.storage.used_bytes)} hint="Measured with pg_database_size across all databases."
              sub={`of ${formatMB(s.storage.allocated_mb)} allocated${s.storage.measured_at ? ` · ${relativeTime(s.storage.measured_at)}` : ""}`} />
            <Stat label="Active connections" icon={Plug} value={formatNumber(s.connections.active)} hint="Measured from pg_stat_activity at the last sample."
              sub={s.connections.measured_at ? `measured ${relativeTime(s.connections.measured_at)}` : "no measurements yet"} />
            <Stat label="Estimated next bill" icon={CreditCard} value={formatCents(s.estimate.amount_cents)}
              sub={s.estimate.next_billing_date ? `on ${formatDate(s.estimate.next_billing_date)} · ${s.quotas.plan.name} plan` : `${s.quotas.plan.name} plan · no upcoming charge`} />
          </>
        )}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <div>
              <CardTitle>Storage used</CardTitle>
              <CardDescription>Daily peak across all databases, last 30 days · measured</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            {storage.data ? <TimeSeriesChart data={storage.data.points} name="Storage used" format={(v) => formatBytes(v, 0)} /> : <Skeleton className="h-[200px]" />}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Plan limits</CardTitle>
              <CardDescription>{s ? `${s.quotas.plan.name} plan` : "…"}</CardDescription>
            </div>
            <Button variant="ghost" size="sm" asChild><Link href="/billing">Upgrade</Link></Button>
          </CardHeader>
          <CardContent>{s ? <QuotaList items={s.quotas.items} /> : <Skeleton className="h-40" />}</CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>API requests</CardTitle>
              <CardDescription>Last 14 days · {s ? formatNumber(s.api_requests.current_period) : "…"} this period</CardDescription>
            </div>
            <Zap className="size-4 text-subtle" aria-hidden />
          </CardHeader>
          <CardContent>
            {requests.data ? (
              <TimeSeriesChart data={fillDays(requests.data.points, 14)} name="API requests" kind="bar" height={160} format={(v) => formatNumber(v)} emptyMessage="No API requests recorded yet. Counts are flushed every minute." />
            ) : <Skeleton className="h-40" />}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Recent database events</CardTitle></CardHeader>
          <CardContent>
            {s?.recent_database_events.length === 0 && <p className="py-8 text-center text-[13px] text-subtle">No events yet.</p>}
            <ul className="space-y-3">
              {s?.recent_database_events.slice(0, 6).map((e) => (
                <li key={e.id} className="text-[13px]">
                  <Link href={`/databases/${e.database_id}`} className="font-medium hover:underline">{e.database_name}</Link>{" "}
                  <span className="text-muted">{eventLabel(e)}</span>
                  <div className="text-xs text-subtle">{relativeTime(e.created_at)}</div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Recent activity</CardTitle>
            <Link href="/activity" className="text-xs text-muted hover:text-foreground">View all</Link>
          </CardHeader>
          <CardContent>
            <ul className="space-y-3">
              {s?.recent_activity.slice(0, 6).map((a) => (
                <li key={a.id} className="text-[13px]">
                  <span className={a.outcome === "failure" ? "text-critical" : undefined}>{actionLabel(a.action)}</span>
                  <div className="text-xs text-subtle">{ACTOR_LABELS[a.actor_type] ?? a.actor_type} · {relativeTime(a.created_at)}</div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>

      {s && s.counts.databases > 0 && (
        <Link href="/backups" className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-border bg-surface px-4 py-3 text-[13px] transition-colors hover:border-border-strong">
          <span className="flex items-center gap-2 font-medium"><DatabaseBackup className="size-4 text-muted" aria-hidden /> Backups</span>
          <span className="text-muted">Last: <span className="text-foreground">{s.backups.last_completed_at ? relativeTime(s.backups.last_completed_at) : "none yet"}</span></span>
          <span className="text-muted">Restorable: <span className="tabular text-foreground">{s.backups.count}</span> ({formatBytes(s.backups.stored_bytes)})</span>
          <span className="text-muted">Retention: <span className="text-foreground">{s.backups.retention_days} days</span></span>
          {s.backups.failed_24h > 0 && <span className="text-critical">{s.backups.failed_24h} failed in 24 h</span>}
          <ArrowRight className="ml-auto size-3.5 text-subtle" aria-hidden />
        </Link>
      )}

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { href: "/projects?new=1", icon: FolderPlus, t: "Create project" },
          { href: "/databases/new", icon: Database, t: "Create database" },
          { href: "/docs", icon: BookOpen, t: "View documentation" },
          { href: "/billing", icon: CreditCard, t: "Manage billing" },
        ].map((q) => (
          <Link key={q.href} href={q.href} className="group flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3 text-[13px] font-medium transition-colors hover:border-border-strong">
            <q.icon className="size-4 text-muted" aria-hidden /> {q.t}
            <ArrowRight className="ml-auto size-3.5 text-subtle transition-transform group-hover:translate-x-0.5" aria-hidden />
          </Link>
        ))}
      </div>
    </>
  );
}
