"use client";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Info, Server, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Meter } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import type { BackupOverview } from "@/features/admin/backup-types";
import { api } from "@/lib/api";
import { formatBytes, formatMB, formatNumber, relativeTime } from "@/lib/utils";

interface StorageStatus { backend: string; hard_limits: boolean; agent?: string; data_plane_fs?: { fs_size_bytes: number; fs_used_bytes: number; fs_available_bytes: number; volumes: number } }
interface Cap { storage?: StorageStatus; host_name: string; instances: number; running: number; max_instances: number; allocated_memory_mb: number; memory_budget_mb: number; allocated_cpus: number; cpu_budget: number; allocated_disk_mb: number; disk_budget_mb: number; versions: string[]; runtime?: string; host_cpus?: number; host_memory_mb?: number; kernel?: string; os?: string }
interface Infra {
  provisioner: { reachable: boolean; capacity: Cap | null };
  gateway: { status: string; uptime_seconds?: number; active_total?: number; counters?: Record<string, number> } | null;
  hosts: { id: string; name: string; provider: string; region: string; status: string; endpoint: string; last_heartbeat_at: string | null; capacity: Cap }[];
  workers: { id: string; started_at: string; last_seen_at: string; healthy: boolean; info: { concurrency?: number; processed?: number } }[];
  versions: { version: string; count: number }[];
  profiles: { profile: string; count: number }[];
  queue: { queued: number; running: number; oldest_queued_at: string | null };
  backups: BackupOverview;
  alerts: { severity: "critical" | "warning" | "info"; message: string }[];
}

function CapRow({ label, used, total, unit }: { label: string; used: number; total: number; unit: (n: number) => string }) {
  const p = total ? (used / total) * 100 : 0;
  return (
    <div>
      <div className="mb-1 flex justify-between text-[13px]"><span>{label}</span><span className="tabular text-muted">{unit(used)} / {unit(total)} ({p.toFixed(0)}%)</span></div>
      <Meter value={p} label={`${label} allocation`} />
    </div>
  );
}

export default function AdminInfrastructure() {
  const q = useQuery({ queryKey: ["admin", "infra"], queryFn: () => api<Infra>("/admin/infrastructure"), refetchInterval: 15_000 });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  if (!d) return <Skeleton className="h-96" />;
  const cap = d.provisioner.capacity;
  const icon = { critical: <XCircle className="size-4 text-critical" />, warning: <AlertTriangle className="size-4 text-warning" />, info: <Info className="size-4 text-accent" /> };
  return (
    <>
      <PageHeader title="Infrastructure" description="Data-plane hosts, capacity, workers, queue and alerts. Allocations are what customers reserved; host totals come from each host." />
      <Card className="mb-4">
        <CardHeader><CardTitle>Alerts</CardTitle></CardHeader>
        <CardContent>
          <ul className="space-y-2">
            {d.alerts.map((a, i) => <li key={i} className="flex items-center gap-2 text-[13px]">{icon[a.severity]} {a.message}</li>)}
          </ul>
        </CardContent>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        {d.hosts.filter((h) => h.status !== "retired").map((h) => (
          <Card key={h.id}>
            <CardHeader>
              <div><CardTitle className="flex items-center gap-2"><Server className="size-4" /> {h.name}</CardTitle>
                <CardDescription>{h.provider} · {h.region} · heartbeat {relativeTime(h.last_heartbeat_at)}</CardDescription></div>
              <StatusBadge status={h.status} />
            </CardHeader>
            <CardContent className="space-y-4">
              {h.capacity.memory_budget_mb === undefined ? <p className="text-[13px] text-subtle">No capacity report yet.</p> : <>
              <CapRow label="Memory (customer budget)" used={h.capacity.allocated_memory_mb} total={h.capacity.memory_budget_mb} unit={formatMB} />
              <CapRow label="CPU (customer budget)" used={h.capacity.allocated_cpus} total={h.capacity.cpu_budget} unit={(n) => `${n} vCPU`} />
              <CapRow label="Disk (customer budget)" used={h.capacity.allocated_disk_mb} total={h.capacity.disk_budget_mb} unit={formatMB} />
              <CapRow label="Instances" used={h.capacity.instances} total={h.capacity.max_instances} unit={(n) => String(n)} />
              <p className="text-xs text-subtle">Host: {h.capacity.host_cpus ?? "?"} CPUs · {formatMB(h.capacity.host_memory_mb ?? 0)} RAM · {h.capacity.runtime ?? "systemd"} · Linux {h.capacity.kernel ?? "?"} · {h.capacity.os ?? ""}</p>
              </>}
            </CardContent>
          </Card>
        ))}
        <Card>
          <CardHeader><CardTitle>Services</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-[13px]">
            <div className="flex justify-between">Provisioner {d.provisioner.reachable ? <Badge variant="good"><CheckCircle2 /> reachable</Badge> : <Badge variant="critical"><XCircle /> unreachable</Badge>}</div>
            <div className="flex justify-between">Gateway {d.gateway?.status === "ok" ? <Badge variant="good"><CheckCircle2 /> ok</Badge> : <Badge variant={d.gateway ? "critical" : "neutral"}>{d.gateway?.status ?? "not monitored"}</Badge>}</div>
            {d.gateway?.counters && (
              <div className="grid grid-cols-3 gap-2 rounded-md bg-surface-2 p-3 text-xs">
                <div><div className="text-subtle">Active</div><div className="tabular font-medium">{d.gateway.active_total}</div></div>
                {Object.entries(d.gateway.counters).map(([k, v]) => <div key={k}><div className="text-subtle">{k.replace("_", " ")}</div><div className="tabular font-medium">{formatNumber(v)}</div></div>)}
              </div>
            )}
            <div className="flex justify-between">Queue <span className="tabular">{d.queue.queued} queued · {d.queue.running} running</span></div>
            <div className="flex justify-between">Oldest queued job <span>{relativeTime(d.queue.oldest_queued_at)}</span></div>
            <div className="flex justify-between">Storage limits
              {d.provisioner.capacity?.storage?.hard_limits
                ? <Badge variant={d.provisioner.capacity.storage.agent === "ok" ? "good" : "critical"}>{d.provisioner.capacity.storage.agent === "ok" ? <CheckCircle2 /> : <XCircle />} hard · agent {d.provisioner.capacity.storage.agent}</Badge>
                : <Badge variant="warning">soft only</Badge>}</div>
            {d.provisioner.capacity?.storage?.data_plane_fs && (
              <CapRow label={`Data-plane disk (${d.provisioner.capacity.storage.data_plane_fs.volumes} volumes)`} used={d.provisioner.capacity.storage.data_plane_fs.fs_used_bytes / 2 ** 20}
                total={d.provisioner.capacity.storage.data_plane_fs.fs_size_bytes / 2 ** 20} unit={formatMB} />
            )}
            <div className="flex justify-between">Backup store <span className="tabular">{d.backups.provisioner ? `${d.backups.provisioner.store} · key ${d.backups.provisioner.active_key_id}` : "unknown"}</span></div>
            <div className="flex justify-between">Backup storage used
              <span className="tabular">{d.backups.provisioner?.used_bytes != null ? formatBytes(d.backups.provisioner.used_bytes) : "—"}{d.backups.provisioner?.budget_mb ? ` of ${formatMB(d.backups.provisioner.budget_mb)}` : ""}</span></div>
          </CardContent>
        </Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Workers</CardTitle></CardHeader>
          <Table>
            <THead><TR><TH>Worker</TH><TH>Health</TH><TH>Started</TH><TH>Last heartbeat</TH><TH>Jobs processed</TH></TR></THead>
            <TBody>
              {d.workers.map((w) => (
                <TR key={w.id}>
                  <TD className="font-mono text-xs">{w.id}</TD>
                  <TD>{w.healthy ? <Badge variant="good">healthy</Badge> : <Badge variant="neutral">stale</Badge>}</TD>
                  <TD className="text-muted">{relativeTime(w.started_at)}</TD>
                  <TD className="text-muted">{relativeTime(w.last_seen_at)}</TD>
                  <TD className="tabular">{w.info.processed ?? 0}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </Card>
        <Card>
          <CardHeader><CardTitle>Inventory</CardTitle></CardHeader>
          <CardContent className="space-y-4 text-[13px]">
            <div><div className="mb-1 text-xs text-subtle">PostgreSQL versions (offered: {cap?.versions.join(", ") ?? "?"})</div>
              {d.versions.map((v) => <div key={v.version} className="flex justify-between"><span>PostgreSQL {v.version}</span><span className="tabular">{v.count}</span></div>)}</div>
            <div><div className="mb-1 text-xs text-subtle">Compute profiles</div>
              {d.profiles.map((p) => <div key={p.profile} className="flex justify-between capitalize"><span>{p.profile}</span><span className="tabular">{p.count}</span></div>)}</div>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
