"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import * as React from "react";

import { TimeSeriesChart } from "@/components/charts/time-series";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { NativeSelect } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Meter } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Stat } from "@/components/ui/stat";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { QuotaList } from "@/features/app/quota-list";
import { api } from "@/lib/api";
import type { Quotas, SeriesPoint } from "@/lib/types";
import { fillDays, formatBytes, formatDate, formatMB, formatNumber, relativeTime } from "@/lib/utils";

interface Current {
  period: { start: string; end: string };
  measured_at: string | null;
  projects: number;
  databases: number;
  allocated_storage_mb: number;
  storage_used_bytes: number;
  active_connections: number;
  api_requests: { current_period: number; previous_period: number };
  peak_storage_bytes: { current_period: number | null; previous_period: number | null };
  by_project: { project_id: string; name: string; databases: number; allocated_storage_mb: number; storage_used_bytes: number; connections: number }[];
  by_database: { database_id: string; name: string; status: string; allocated_storage_mb: number; storage_used_bytes: number | null; connections: number | null; max_connections: number; storage_percent: number | null; measured_at: string | null }[];
}

function delta(cur: number | null, prev: number | null) {
  if (cur === null || prev === null || prev === 0) return null;
  const d = ((cur - prev) / prev) * 100;
  return `${d >= 0 ? "+" : ""}${d.toFixed(0)}% vs previous period`;
}

function History({ metric, title, fmt, kind = "area" as "area" | "bar", days }: { metric: string; title: string; fmt: (v: number) => string; kind?: "area" | "bar"; days: number }) {
  const q = useQuery({ queryKey: ["usage-history", metric, days], queryFn: () => api<{ points: SeriesPoint[] }>("/usage/history", { query: { metric, days, bucket: days <= 2 ? "hour" : "day" } }) });
  return (
    <Card>
      <CardHeader><div><CardTitle>{title}</CardTitle><CardDescription>Measured · account total</CardDescription></div></CardHeader>
      <CardContent>{q.data ? <TimeSeriesChart data={metric === "api_requests" && days > 2 ? fillDays(q.data.points, days) : q.data.points} name={title} format={fmt} kind={kind} withTime={days <= 2} height={190} /> : <Skeleton className="h-[190px]" />}</CardContent>
    </Card>
  );
}

export default function UsagePage() {
  const [days, setDays] = React.useState(30);
  const cur = useQuery({ queryKey: ["usage-current"], queryFn: () => api<Current>("/usage/current"), refetchInterval: 60_000 });
  const quotas = useQuery({ queryKey: ["usage-quotas"], queryFn: () => api<Quotas>("/usage/quotas") });
  if (cur.isError) return <ErrorState error={cur.error} onRetry={() => cur.refetch()} />;
  const c = cur.data;
  return (
    <>
      <PageHeader title="Usage" description={c ? `Current period ${formatDate(c.period.start)} – ${formatDate(c.period.end)}${c.measured_at ? ` · last measured ${relativeTime(c.measured_at)}` : ""}` : "Loading…"}
        actions={
          <NativeSelect aria-label="Time range" className="w-40" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={1}>Last 24 hours</option><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
          </NativeSelect>
        } />
      <div className="mb-3 flex flex-wrap gap-2 text-xs text-muted">
        <Badge variant="outline">measured</Badge> read from PostgreSQL and the instance ·
        <Badge variant="outline">allocated</Badge> reserved by your settings ·
        <Badge variant="outline">count</Badge> from your account
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {!c ? [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28" />) : (
          <>
            <Stat label="Storage used" value={formatBytes(c.storage_used_bytes)} sub={`measured · of ${formatMB(c.allocated_storage_mb)} allocated`} />
            <Stat label="Peak storage this period" value={formatBytes(c.peak_storage_bytes.current_period)} sub={delta(c.peak_storage_bytes.current_period, c.peak_storage_bytes.previous_period) ?? "no previous-period data"} />
            <Stat label="Active connections" value={formatNumber(c.active_connections)} sub="measured at the last sample" />
            <Stat label="API requests" value={formatNumber(c.api_requests.current_period)} sub={delta(c.api_requests.current_period, c.api_requests.previous_period) ?? `${formatNumber(c.api_requests.previous_period)} last period`} />
          </>
        )}
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <History metric="storage_bytes" title="Storage used" fmt={(v) => formatBytes(v, 0)} days={days} />
        <History metric="connections" title="Active connections" fmt={(v) => formatNumber(Math.round(v))} days={days} />
        <History metric="api_requests" title="API requests" fmt={(v) => formatNumber(v)} kind="bar" days={days} />
        <Card>
          <CardHeader><div><CardTitle>Plan limits</CardTitle><CardDescription>{quotas.data?.plan.name} plan · alerts at 70%, 85% and 100%</CardDescription></div>
            <Link href="/billing" className="text-xs text-muted hover:text-foreground">Change plan</Link></CardHeader>
          <CardContent>{quotas.data ? <QuotaList items={quotas.data.items} /> : <Skeleton className="h-40" />}</CardContent>
        </Card>
      </div>
      <Card className="mt-4">
        <CardHeader><CardTitle>By database</CardTitle></CardHeader>
        <Table>
          <THead><TR><TH>Database</TH><TH>Status</TH><TH>Storage used</TH><TH className="w-48">Of allocation</TH><TH>Connections</TH><TH>Measured</TH></TR></THead>
          <TBody>
            {c?.by_database.map((d) => (
              <TR key={d.database_id}>
                <TD><Link href={`/databases/${d.database_id}?tab=usage`} className="font-medium hover:underline">{d.name}</Link></TD>
                <TD><StatusBadge status={d.status} /></TD>
                <TD className="tabular">{d.storage_used_bytes === null ? "—" : formatBytes(d.storage_used_bytes)} <span className="text-subtle">/ {formatMB(d.allocated_storage_mb)}</span></TD>
                <TD><Meter value={d.storage_percent ?? 0} label={`${d.name} storage`} /></TD>
                <TD className="tabular text-muted">{d.connections ?? "—"} / {d.max_connections}</TD>
                <TD className="text-muted">{relativeTime(d.measured_at)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
        {c && c.by_database.length === 0 && <p className="p-6 text-center text-[13px] text-subtle">No databases yet.</p>}
      </Card>
      <Card className="mt-4">
        <CardHeader><CardTitle>By project</CardTitle></CardHeader>
        <Table>
          <THead><TR><TH>Project</TH><TH>Databases</TH><TH>Storage used</TH><TH>Allocated</TH><TH>Connections</TH></TR></THead>
          <TBody>
            {c?.by_project.map((p) => (
              <TR key={p.project_id}>
                <TD><Link href={`/projects/${p.project_id}`} className="font-medium hover:underline">{p.name}</Link></TD>
                <TD className="tabular">{p.databases}</TD>
                <TD className="tabular">{formatBytes(p.storage_used_bytes)}</TD>
                <TD className="tabular text-muted">{formatMB(p.allocated_storage_mb)}</TD>
                <TD className="tabular text-muted">{p.connections}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </Card>
    </>
  );
}
