"use client";
import { useQuery } from "@tanstack/react-query";
import * as React from "react";

import { TimeSeriesChart } from "@/components/charts/time-series";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { Database, SeriesPoint } from "@/lib/types";
import { formatBytes, formatNumber } from "@/lib/utils";

const METRICS = [
  { id: "storage_bytes", title: "Storage used", desc: "pg_database_size", fmt: (v: number) => formatBytes(v, 0) },
  { id: "connections", title: "Active connections", desc: "pg_stat_activity for your role", fmt: (v: number) => formatNumber(Math.round(v)) },
  { id: "memory_bytes", title: "Memory", desc: "instance cgroup, excluding page cache", fmt: (v: number) => formatBytes(v, 0) },
  { id: "cpu_percent", title: "CPU", desc: "percent of one host CPU", fmt: (v: number) => `${v.toFixed(1)}%` },
  { id: "transactions_per_minute", title: "Queries (transactions) per minute", desc: "commits + rollbacks, pg_stat_database", fmt: (v: number) => formatNumber(Math.round(v)) },
  { id: "rows_read_per_minute", title: "Rows read per minute", desc: "rows returned and fetched by queries", fmt: (v: number) => formatNumber(Math.round(v)) },
  { id: "rows_written_per_minute", title: "Rows written per minute", desc: "inserted + updated + deleted", fmt: (v: number) => formatNumber(Math.round(v)) },
] as const;

function MetricCard({ id, metric, days }: { id: string; metric: (typeof METRICS)[number]; days: number }) {
  const q = useQuery({
    queryKey: ["db-usage", id, metric.id, days],
    queryFn: () => api<{ points: SeriesPoint[] }>(`/databases/${id}/usage`, { query: { metric: metric.id, days } }),
  });
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{metric.title}</CardTitle>
          <CardDescription>Measured · {metric.desc}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>{q.data ? <TimeSeriesChart data={q.data.points} name={metric.title} format={metric.fmt} withTime height={180} /> : <Skeleton className="h-[180px]" />}</CardContent>
    </Card>
  );
}

export function UsageTab({ database }: { database: Database }) {
  const [days, setDays] = React.useState(7);
  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-4">
        <p className="text-[13px] text-muted">Samples are taken every few minutes while the database is running. Older data is rolled up into hourly averages.</p>
        <NativeSelect aria-label="Time range" className="w-36" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={1}>Last 24 hours</option><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option>
        </NativeSelect>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {METRICS.map((m) => <MetricCard key={m.id} id={database.id} metric={m} days={days} />)}
      </div>
    </div>
  );
}
