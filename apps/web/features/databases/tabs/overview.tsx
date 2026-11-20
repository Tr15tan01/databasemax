"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Clock, Cpu, HardDrive, MemoryStick, Plug } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Meter } from "@/components/ui/progress";
import { Stat } from "@/components/ui/stat";
import { api } from "@/lib/api";
import { eventLabel } from "@/lib/labels";
import type { Database, DbEvent, Page, SeriesPoint } from "@/lib/types";
import { formatBytes, formatDateTime, formatMB, pct, relativeTime } from "@/lib/utils";

function uptime(since: string | null | undefined) {
  if (!since) return "—";
  const s = (Date.now() - new Date(since).getTime()) / 1000;
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

function useLatest(id: string, metric: string) {
  return useQuery({
    queryKey: ["db-usage", id, metric, 1],
    queryFn: () => api<{ points: SeriesPoint[] }>(`/databases/${id}/usage`, { query: { metric, days: 1 } }),
    select: (d) => d.points.at(-1),
  });
}

export function OverviewTab({ database: d }: { database: Database }) {
  const events = useQuery({ queryKey: ["db-events", d.id, 1], queryFn: () => api<Page<DbEvent>>(`/databases/${d.id}/events`, { query: { page_size: 8 } }) });
  const mem = useLatest(d.id, "memory_bytes");
  const cpu = useLatest(d.id, "cpu_percent");
  const used = d.storage_used_bytes ?? 0;
  const running = d.status === "ready";
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Storage" icon={HardDrive} value={d.storage_used_bytes === null ? "—" : formatBytes(used)} sub={`of ${formatMB(d.allocated_storage_mb)} allocated`}>
          <Meter className="mt-3" value={pct(used, d.allocated_storage_mb * 1024 * 1024)} label="Storage used" />
        </Stat>
        <Stat label="Connections" icon={Plug} value={d.connections_current ?? "—"} sub={`limit ${d.max_connections}`}>
          <Meter className="mt-3" value={pct(d.connections_current ?? 0, d.max_connections)} label="Connections used" />
        </Stat>
        <Stat label="Memory" icon={MemoryStick} hint="Measured from the instance's cgroup (excludes page cache)."
          value={running && mem.data ? formatBytes(mem.data.v) : "—"} sub={d.memory_mb ? `of ${d.memory_mb} MB limit` : undefined} />
        <Stat label="CPU" icon={Cpu} hint="Measured average over a short sample; relative to one host CPU."
          value={running && cpu.data ? `${cpu.data.v.toFixed(1)}%` : "—"} sub={d.cpus ? `${d.cpus} vCPU limit` : undefined} />
      </div>
      <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
        <Card>
          <CardHeader><CardTitle>Details</CardTitle></CardHeader>
          <CardContent>
            <dl className="grid grid-cols-[140px_1fr] gap-y-2.5 text-[13px]">
              {[
                ["PostgreSQL", d.postgres_version],
                ["Size", `${d.compute_profile} · ${d.cpus} vCPU · ${d.memory_mb} MB`],
                ["Region", d.region_label ?? d.region],
                ["Environment", d.environment],
                ["Uptime", running ? uptime(d.started_at) : "Not running"],
                ["Created", formatDateTime(d.created_at)],
                ["Ready since", formatDateTime(d.ready_at)],
                ["Last activity", relativeTime(d.last_activity_at)],
                ["Last measured", d.metrics_measured_at ? relativeTime(d.metrics_measured_at) : "Not yet"],
                ["Database ID", d.id],
              ].map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-muted">{k}</dt>
                  <dd className={k === "Database ID" ? "truncate font-mono text-xs" : undefined}>{v}</dd>
                </div>
              ))}
            </dl>
            {d.source_database_id && (
              <p className="mt-4 rounded-md bg-surface-2 px-3 py-2 text-xs text-muted">
                Created as a copy of <Link className="font-medium text-foreground hover:underline" href={`/databases/${d.source_database_id}`}>another database</Link>
                {d.restored_from_backup_id ? " from one of its backups" : ""}
                {d.data_restored_at ? ` · data restored ${formatDateTime(d.data_restored_at)}` : " · restore pending"}. It has its own credentials and storage.
              </p>
            )}
            {d.storage_over_limit && (
              <p className="mt-4 rounded-md bg-critical-soft px-3 py-2 text-xs text-critical">
                Storage limit reached — new sessions default to read-only. Free space or increase the allocation in Settings.
              </p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Recent events</CardTitle><Clock className="size-4 text-subtle" /></CardHeader>
          <CardContent>
            <ol className="relative space-y-4 border-l border-border pl-5">
              {events.data?.items.map((e) => (
                <li key={e.id} className="text-[13px]">
                  <span className="absolute -left-[5px] mt-1.5 size-2.5 rounded-full border-2 border-surface bg-border-strong" aria-hidden />
                  <div className="font-medium">{eventLabel(e)}</div>
                  {e.message && <div className="text-muted">{e.message}</div>}
                  <div className="text-xs text-subtle">{formatDateTime(e.created_at)} · {e.actor_type}</div>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
