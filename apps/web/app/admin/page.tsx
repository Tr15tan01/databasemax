"use client";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Clock, CreditCard, Database, HardDrive, ShieldAlert, UserPlus, Users, XCircle, Zap } from "lucide-react";
import Link from "next/link";

import { TimeSeriesChart } from "@/components/charts/time-series";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Stat } from "@/components/ui/stat";
import { StatusBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import type { BackupOverview } from "@/features/admin/backup-types";
import { fillDays, formatBytes, formatCents, formatMB, formatNumber, relativeTime } from "@/lib/utils";

interface Overview {
  users: { total: number; verified: number; active_24h: number; active_30d: number; new_7d: number; new_30d: number; suspended: number };
  projects: number;
  databases: { total: number; by_status: Record<string, number>; running: number; failed: number };
  storage: { allocated_mb: number; used_bytes: number };
  provisioning: { queued: number; running: number; success_rate_30d: number | null; succeeded_30d: number; failed_30d: number; p50_seconds: number | null; p95_seconds: number | null };
  billing: { mrr_cents: number; paid_subscriptions: number; payment_failures_30d: number; by_plan: { plan: string; status: string; count: number }[]; mrr_note: string };
  security: { failed_logins_24h: number; throttled_24h: number };
  api: { requests_24h: number; requests_7d: number; series_14d: { date: string; value: number }[] };
  infrastructure: { hosts: { name: string; status: string; last_heartbeat_at: string | null }[]; worker_last_seen_at: string | null; worker_healthy: boolean };
  backups: BackupOverview;
  series: Record<string, { date: string; value: number }[]>;
  generated_at: string;
}

const toPoints = (s: { date: string; value: number }[] | undefined) => (s ?? []).map((p) => ({ t: p.date, v: p.value }));

export default function AdminOverview() {
  const q = useQuery({ queryKey: ["admin", "overview"], queryFn: () => api<Overview>("/admin/overview"), refetchInterval: 30_000 });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const o = q.data;
  if (!o) return <div className="grid gap-4 md:grid-cols-4">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-28" />)}</div>;
  const storagePct = o.storage.allocated_mb ? (o.storage.used_bytes / (o.storage.allocated_mb * 1024 * 1024)) * 100 : 0;
  return (
    <>
      <PageHeader title="Platform overview" description={`Live data from the control plane · updated ${relativeTime(o.generated_at)}`} />

      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-subtle">Users</h2>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Registered users" icon={Users} value={formatNumber(o.users.total)} sub={`${formatNumber(o.users.verified)} verified · ${o.users.suspended} suspended`} />
        <Stat label="Active users" icon={Zap} value={formatNumber(o.users.active_30d)} sub={`${o.users.active_24h} in the last 24h · by session activity`} />
        <Stat label="New registrations" icon={UserPlus} value={formatNumber(o.users.new_7d)} sub={`last 7 days · ${o.users.new_30d} in 30 days`} />
        <Stat label="Projects" value={formatNumber(o.projects)} sub="excluding deleted" />
      </div>

      <h2 className="mb-3 mt-8 text-xs font-semibold uppercase tracking-wide text-subtle">Databases & provisioning</h2>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Databases" icon={Database} value={formatNumber(o.databases.total)} sub={`${o.databases.running} running · ${o.databases.failed} failed`} />
        <Stat label="Provisioning queue" icon={Clock} value={o.provisioning.queued + o.provisioning.running} sub={`${o.provisioning.queued} queued · ${o.provisioning.running} running`} />
        <Stat label="Creation success (30d)" icon={CheckCircle2} value={o.provisioning.success_rate_30d === null ? "—" : `${o.provisioning.success_rate_30d}%`}
          sub={`${o.provisioning.succeeded_30d} succeeded · ${o.provisioning.failed_30d} failed`} />
        <Stat label="Provisioning time" value={o.provisioning.p50_seconds === null ? "—" : `${o.provisioning.p50_seconds}s`}
          sub={o.provisioning.p95_seconds === null ? "no completed jobs yet" : `median · p95 ${o.provisioning.p95_seconds}s`} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader><div><CardTitle>Registrations</CardTitle><CardDescription>New accounts per day, last 30 days</CardDescription></div></CardHeader>
          <CardContent><TimeSeriesChart data={toPoints(o.series.registrations_30d)} name="Registrations" kind="bar" height={180} /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Databases by status</CardTitle></CardHeader>
          <CardContent>
            <ul className="space-y-2.5">
              {Object.entries(o.databases.by_status).sort((a, b) => b[1] - a[1]).map(([s, n]) => (
                <li key={s} className="flex items-center justify-between text-[13px]"><StatusBadge status={s} /><span className="tabular font-medium">{n}</span></li>
              ))}
              {Object.keys(o.databases.by_status).length === 0 && <li className="text-[13px] text-subtle">No databases yet.</li>}
            </ul>
          </CardContent>
        </Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><div><CardTitle>Databases created</CardTitle><CardDescription>Per day, last 30 days</CardDescription></div></CardHeader>
          <CardContent><TimeSeriesChart data={toPoints(o.series.databases_created_30d)} name="Databases created" kind="bar" height={160} /></CardContent>
        </Card>
        <Card>
          <CardHeader><div><CardTitle>Failed provisioning jobs</CardTitle><CardDescription>Per day, last 30 days</CardDescription></div></CardHeader>
          <CardContent><TimeSeriesChart data={toPoints(o.series.failed_jobs_30d)} name="Failed jobs" kind="bar" height={160} /></CardContent>
        </Card>
      </div>

      <h2 className="mb-3 mt-8 text-xs font-semibold uppercase tracking-wide text-subtle">Resources, revenue & security</h2>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Storage" icon={HardDrive} value={formatBytes(o.storage.used_bytes)} sub={`measured · ${formatMB(o.storage.allocated_mb)} allocated (${storagePct.toFixed(1)}%)`} />
        <Stat label="Monthly recurring revenue" icon={CreditCard} value={formatCents(o.billing.mrr_cents)} hint={o.billing.mrr_note} sub={`${o.billing.paid_subscriptions} paid subscriptions`} />
        <Stat label="Payment failures" icon={AlertTriangle} value={o.billing.payment_failures_30d} sub="last 30 days" />
        <Stat label="Failed sign-ins" icon={ShieldAlert} value={o.security.failed_logins_24h} sub={`last 24h · ${o.security.throttled_24h} throttled`} />
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader><div><CardTitle>API requests</CardTitle><CardDescription>{formatNumber(o.api.requests_24h)} in 24h · {formatNumber(o.api.requests_7d)} in 7 days · authenticated requests only</CardDescription></div></CardHeader>
          <CardContent><TimeSeriesChart data={fillDays(toPoints(o.api.series_14d), 14)} name="API requests" kind="bar" height={160} format={(v) => formatNumber(v)} /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Health</CardTitle><Link href="/admin/infrastructure" className="text-xs text-muted hover:text-foreground">Details</Link></CardHeader>
          <CardContent className="space-y-3 text-[13px]">
            <div className="flex items-center justify-between">Workers
              {o.infrastructure.worker_healthy ? <Badge variant="good"><CheckCircle2 /> Healthy</Badge> : <Badge variant="critical"><XCircle /> No heartbeat</Badge>}</div>
            {o.infrastructure.hosts.map((h) => (
              <div key={h.name} className="flex items-center justify-between">Host {h.name}
                <Badge variant={h.status === "active" ? "good" : "critical"}>{h.status === "active" ? <CheckCircle2 /> : <XCircle />} {h.status}</Badge></div>
            ))}
            <div className="flex items-center justify-between">Backup coverage
              {o.backups.ready_databases === 0 ? <Badge variant="neutral">no running databases</Badge>
                : o.backups.ready_databases_with_recent_backup >= o.backups.ready_databases
                  ? <Badge variant="good"><CheckCircle2 /> {o.backups.ready_databases}/{o.backups.ready_databases} in 26 h</Badge>
                  : <Badge variant="warning"><AlertTriangle /> {o.backups.ready_databases_with_recent_backup}/{o.backups.ready_databases} in 26 h</Badge>}</div>
            <div className="flex items-center justify-between">Restore checks (30 d)
              {o.backups.verification_30d.failed > 0 ? <Badge variant="critical"><XCircle /> {o.backups.verification_30d.failed} failed</Badge>
                : <Badge variant={o.backups.verification_30d.passed ? "good" : "neutral"}>{o.backups.verification_30d.passed} passed</Badge>}</div>
            <p className="text-xs text-subtle">{formatBytes(o.backups.stored_bytes)} of encrypted backups stored · <Link href="/admin/backups" className="hover:text-foreground">details</Link></p>
          </CardContent>
        </Card>
      </div>
      <Card className="mt-4">
        <CardHeader><CardTitle>Subscriptions by plan</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          {o.billing.by_plan.map((b) => (
            <div key={`${b.plan}-${b.status}`} className="rounded-lg border border-border px-4 py-3 text-[13px]">
              <div className="font-medium capitalize">{b.plan}</div><div className="text-xs text-muted">{b.status} · <span className="tabular">{b.count}</span></div>
            </div>
          ))}
        </CardContent>
      </Card>
    </>
  );
}
