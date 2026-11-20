"use client";
import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowDownToLine, ArrowUpFromLine, Cpu, Database as DbIcon, Hourglass, Lock, MemoryStick, Pause, Play, Plug, Zap } from "lucide-react";
import * as React from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip as ChartTooltip, YAxis } from "recharts";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Meter } from "@/components/ui/progress";
import { api } from "@/lib/api";
import type { Database, LiveSample } from "@/lib/types";
import { cn, formatBytes, formatNumber, pct } from "@/lib/utils";

const POLL_MS = 3000;
const WINDOW = 100; // samples kept in memory: 5 minutes at 3 s

type Series = "tps" | "cpu" | "rowsRead" | "rowsWritten" | "netIn" | "netOut" | "sessions";
type Sample = { t: number } & Partial<Record<Series, number>>;

function rate(v: number | null | undefined, unit = "/s") {
  if (v === null || v === undefined) return "—";
  if (v >= 10_000) return `${(v / 1000).toFixed(v >= 100_000 ? 0 : 1)}k${unit}`;
  return `${v >= 100 ? Math.round(v) : v.toFixed(v >= 10 ? 1 : 2)}${unit}`;
}

function Spark({ data, k, color = "var(--chart-1)", format }: { data: Sample[]; k: Series; color?: string; format: (v: number) => string }) {
  const id = React.useId().replace(/:/g, "");
  return (
    <div className="h-16" aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={`g-${id}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.25} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={[0, (max: number) => Math.max(max * 1.15, 1)]} />
          <ChartTooltip
            cursor={false}
            content={({ active, payload }) =>
              active && payload?.length ? (
                <div className="rounded border border-border bg-surface px-2 py-1 text-[11px] shadow-sm">{format(Number(payload[0]!.value))}</div>
              ) : null
            }
          />
          <Area type="monotone" dataKey={k} stroke={color} strokeWidth={1.5} fill={`url(#g-${id})`} isAnimationActive={false} connectNulls />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function Tile({
  label,
  icon: Icon,
  value,
  sub,
  data,
  k,
  color,
  format,
  children,
}: {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  value: React.ReactNode;
  sub?: React.ReactNode;
  data?: Sample[];
  k?: Series;
  color?: string;
  format?: (v: number) => string;
  children?: React.ReactNode;
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between text-[13px] text-muted">
        <span>{label}</span>
        <Icon className="size-4 text-subtle" />
      </div>
      <div className="mt-1.5 text-2xl font-semibold tabular-nums tracking-[-0.02em]">{value}</div>
      {sub && <div className="text-xs text-subtle">{sub}</div>}
      {children}
      {data && k && <div className="mt-2"><Spark data={data} k={k} color={color} format={format ?? String} /></div>}
    </Card>
  );
}

export function LiveTab({ database: d }: { database: Database }) {
  const [paused, setPaused] = React.useState(false);
  const [samples, setSamples] = React.useState<Sample[]>([]);
  const running = d.status === "ready";
  const q = useQuery({
    queryKey: ["db-live", d.id],
    queryFn: () => api<LiveSample>(`/databases/${d.id}/live`),
    enabled: running && !paused,
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false, // stop polling while the browser tab is hidden
    retry: 1,
  });
  const s = q.data;
  React.useEffect(() => {
    if (!s) return;
    const sessions = (s.sessions_active ?? 0) + (s.sessions_idle ?? 0) + (s.sessions_idle_in_transaction ?? 0);
    const point: Sample = {
      t: new Date(s.measured_at).getTime(),
      tps: s.transactions_per_second ?? undefined,
      cpu: s.cpu_percent_of_limit ?? undefined,
      rowsRead: s.rows_read_per_second ?? undefined,
      rowsWritten: s.rows_written_per_second ?? undefined,
      netIn: s.network_in_bytes_per_second ?? undefined,
      netOut: s.network_out_bytes_per_second ?? undefined,
      sessions,
    };
    setSamples((prev) => (prev.at(-1)?.t === point.t ? prev : [...prev, point].slice(-WINDOW)));
  }, [s]);

  if (!running) return <EmptyState icon={Activity} title="Live view needs a running database" description="Start the database to watch its load in real time." />;
  if (q.isError && !s) return <ErrorState error={q.error} onRetry={() => q.refetch()} title="Couldn't load live metrics" />;

  const sessions = (s?.sessions_active ?? 0) + (s?.sessions_idle ?? 0) + (s?.sessions_idle_in_transaction ?? 0);
  const memPct = s?.memory_bytes && s.memory_limit_bytes ? pct(s.memory_bytes, s.memory_limit_bytes) : null;
  const load = s?.cpu_percent_of_limit ?? null;
  const loadLabel = load === null ? "—" : load >= 90 ? "Saturated" : load >= 60 ? "Busy" : load >= 15 ? "Working" : "Idle";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-[13px]">
          <span className="relative flex size-2.5">
            {!paused && <span className="absolute inline-flex size-full animate-ping rounded-full bg-good opacity-60" />}
            <span className={cn("relative inline-flex size-2.5 rounded-full", paused ? "bg-subtle" : "bg-good")} />
          </span>
          <span className="font-medium">{paused ? "Paused" : "Live"}</span>
          <span className="text-muted">· updates every {POLL_MS / 1000} s while this tab is open · last 5 minutes</span>
        </div>
        <Button variant="secondary" size="sm" onClick={() => setPaused((p) => !p)}>
          {paused ? <Play /> : <Pause />} {paused ? "Resume" : "Pause"}
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Tile label="Load" icon={Cpu} value={load === null ? "—" : `${load.toFixed(0)}%`} sub={`${loadLabel} · CPU of your ${d.cpus ?? "?"} vCPU limit`} data={samples} k="cpu" color="var(--chart-2)" format={(v) => `${v.toFixed(0)}%`}>
          <Meter className="mt-2" value={load ?? 0} label="CPU load" />
        </Tile>
        <Tile label="Transactions" icon={Zap} value={rate(s?.transactions_per_second)} sub={s?.rollbacks_per_second ? `${rate(s.rollbacks_per_second)} rolled back` : "commits + rollbacks per second"} data={samples} k="tps" format={(v) => rate(v)} />
        <Tile label="Network" icon={ArrowDownToLine} value={<span className="text-xl">{s?.network_in_bytes_per_second == null ? "—" : `${formatBytes(s.network_in_bytes_per_second)}/s`}</span>} sub={<span className="inline-flex items-center gap-1"><ArrowUpFromLine className="size-3" /> {s?.network_out_bytes_per_second == null ? "—" : `${formatBytes(s.network_out_bytes_per_second)}/s`} sent to clients</span>} data={samples} k="netOut" color="var(--chart-3)" format={(v) => `${formatBytes(v)}/s`} />
        <Tile label="Connections" icon={Plug} value={`${sessions}`} sub={`${s?.sessions_active ?? 0} active · ${s?.sessions_idle ?? 0} idle · limit ${d.max_connections}`} data={samples} k="sessions" format={(v) => `${Math.round(v)}`}>
          <Meter className="mt-2" value={pct(sessions, d.max_connections)} label="Connections used" />
        </Tile>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Rows per second</CardTitle>
              <CardDescription>Read by queries (blue) and written by INSERT/UPDATE/DELETE (orange)</CardDescription>
            </div>
            <DbIcon className="size-4 text-subtle" />
          </CardHeader>
          <CardContent>
            <div className="mb-2 flex gap-6 text-[13px]">
              <span><span className="font-semibold tabular-nums">{rate(s?.rows_read_per_second)}</span> <span className="text-muted">read</span></span>
              <span><span className="font-semibold tabular-nums">{rate(s?.rows_written_per_second)}</span> <span className="text-muted">written</span></span>
            </div>
            <div className="h-36">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={samples} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                  <YAxis width={44} tick={{ fill: "var(--chart-axis)", fontSize: 10 }} axisLine={false} tickLine={false} tickFormatter={(v: number) => rate(v, "")} />
                  <ChartTooltip formatter={(v) => rate(Number(v))} labelFormatter={() => ""} contentStyle={{ fontSize: 11 }} />
                  <Area type="monotone" dataKey="rowsRead" name="read" stroke="var(--chart-1)" fill="var(--chart-1)" fillOpacity={0.12} isAnimationActive={false} connectNulls />
                  <Area type="monotone" dataKey="rowsWritten" name="written" stroke="var(--chart-2)" fill="var(--chart-2)" fillOpacity={0.12} isAnimationActive={false} connectNulls />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Right now</CardTitle>
              <CardDescription>Point-in-time health signals</CardDescription>
            </div>
            <Activity className="size-4 text-subtle" />
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-[1fr_auto] gap-y-2.5 text-[13px]">
              <dt className="flex items-center gap-2 text-muted"><MemoryStick className="size-3.5" /> Memory</dt>
              <dd className="tabular-nums">{s?.memory_bytes == null ? "—" : `${formatBytes(s.memory_bytes)}${memPct !== null ? ` (${memPct}%)` : ""}`}</dd>
              <dt className="flex items-center gap-2 text-muted"><Zap className="size-3.5" /> Cache hit ratio</dt>
              <dd className={cn("tabular-nums", s?.cache_hit_ratio != null && s.cache_hit_ratio < 0.95 && "text-warning")}>
                {s?.cache_hit_ratio == null ? "—" : `${(s.cache_hit_ratio * 100).toFixed(1)}%`}
              </dd>
              <dt className="flex items-center gap-2 text-muted"><Hourglass className="size-3.5" /> Longest running query</dt>
              <dd className={cn("tabular-nums", (s?.longest_query_seconds ?? 0) > 30 && "text-warning")}>
                {s?.longest_query_seconds == null ? "—" : `${s.longest_query_seconds.toFixed(1)} s`}
              </dd>
              <dt className="flex items-center gap-2 text-muted"><Hourglass className="size-3.5" /> Idle in transaction</dt>
              <dd className={cn("tabular-nums", (s?.sessions_idle_in_transaction ?? 0) > 0 && "text-warning")}>{formatNumber(s?.sessions_idle_in_transaction)}</dd>
              <dt className="flex items-center gap-2 text-muted"><Lock className="size-3.5" /> Waiting on locks</dt>
              <dd className={cn("tabular-nums", (s?.sessions_waiting_on_locks ?? 0) > 0 && "text-warning")}>{formatNumber(s?.sessions_waiting_on_locks)}</dd>
              <dt className="flex items-center gap-2 text-muted"><Lock className="size-3.5" /> Deadlocks (total)</dt>
              <dd className="tabular-nums">{formatNumber(s?.deadlocks_total)}</dd>
              <dt className="flex items-center gap-2 text-muted"><Plug className="size-3.5" /> Connections via gateway</dt>
              <dd className="tabular-nums">{formatNumber(s?.gateway_connections)}</dd>
            </dl>
          </CardContent>
        </Card>
      </div>
      <p className="text-xs text-subtle">
        Rates compare two samples a few seconds apart. Only counts are collected — never query text, data or client addresses.
      </p>
    </div>
  );
}
