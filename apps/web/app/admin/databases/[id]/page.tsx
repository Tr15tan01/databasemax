"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, DatabaseBackup, MoveRight, Play, RotateCw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { MoveDialog } from "@/features/admin/move-dialog";
import { ReasonDialog } from "@/features/admin/reason-dialog";
import { BackupTable } from "@/features/backups/backup-table";
import { useReauth } from "@/features/app/reauth-dialog";
import { api, errorMessage } from "@/lib/api";
import { eventLabel } from "@/lib/labels";
import type { Backup, Database } from "@/lib/types";
import { formatBytes, formatDateTime, formatMB } from "@/lib/utils";

interface Detail {
  database: Database;
  owner: { id: string; email: string; status: string } | null;
  placement: { host: string | null; provider: string; region: string; instance: string; route_id: string; internal_endpoint: string | null };
  events: { id: string; event_type: string; from_status: string | null; to_status: string | null; message: string | null; actor_type: string; created_at: string }[];
  jobs: { id: string; kind: string; status: string; step: string | null; attempt_count: number; max_attempts: number; error_code: string | null; safe_error_message: string | null; created_at: string; completed_at: string | null }[];
  backups: Backup[];
}
type Action = "retry" | "suspend" | "unsuspend" | "backup";

export default function AdminDatabaseDetail() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { run, dialog } = useReauth();
  const [action, setAction] = React.useState<Action | null>(null);
  const [moving, setMoving] = React.useState(false);
  const move = useMutation({
    mutationFn: ({ target, reason }: { target: string; reason: string }) =>
      run(() => api(`/admin/databases/${id}/move`, { method: "POST", body: { target_host_id: target, reason } })),
    onSuccess: () => { toast.success("Move started — recorded in the audit log"); setMoving(false); qc.invalidateQueries({ queryKey: ["admin"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const q = useQuery({ queryKey: ["admin", "database", id], queryFn: () => api<Detail>(`/admin/databases/${id}`), refetchInterval: 5000 });
  const act = useMutation({
    mutationFn: ({ a, reason }: { a: Action; reason: string }) => run(() => api(`/admin/databases/${id}/${a}`, { method: "POST", body: { reason } })),
    onSuccess: () => { toast.success("Done — recorded in the audit log"); setAction(null); qc.invalidateQueries({ queryKey: ["admin"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  if (!d) return <Skeleton className="h-80" />;
  const db = d.database;
  return (
    <>
      {dialog}
      <PageHeader eyebrow={<Link href="/admin/databases" className="hover:text-foreground">Databases</Link>}
        title={<span className="flex items-center gap-3">{db.name} <StatusBadge status={db.status} />{db.suspended_by_admin && <Badge variant="critical">suspended by support</Badge>}</span>}
        description={d.owner ? <>Owner <Link href={`/admin/users/${d.owner.id}`} className="underline">{d.owner.email}</Link> · project {db.project_name}</> : undefined}
        actions={
          <>
            {db.status === "failed" && <Button variant="secondary" onClick={() => setAction("retry")}><RotateCw /> Retry provisioning</Button>}
            {db.status === "ready" && <Button variant="secondary" onClick={() => setAction("backup")}><DatabaseBackup /> Back up now</Button>}
            {db.status === "ready" && <Button variant="secondary" onClick={() => setMoving(true)}><MoveRight /> Move host</Button>}
            {db.suspended_by_admin ? <Button onClick={() => setAction("unsuspend")}><Play /> Lift suspension</Button>
              : db.status === "ready" && <Button variant="danger" onClick={() => setAction("suspend")}><Ban /> Suspend</Button>}
          </>
        } />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Placement</CardTitle></CardHeader>
          <CardContent>
            <dl className="grid grid-cols-[140px_1fr] gap-y-2 text-[13px]">
              {[["Host", d.placement.host ?? "—"], ["Provider", d.placement.provider], ["Region", d.placement.region], ["Instance", d.placement.instance], ["Route ID", d.placement.route_id], ["Public host", db.host], ["Internal endpoint", d.placement.internal_endpoint ?? "—"]].map(([k, v]) => (
                <div key={k} className="contents"><dt className="text-muted">{k}</dt><dd className="truncate font-mono text-xs">{v}</dd></div>
              ))}
            </dl>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Resource allocation</CardTitle></CardHeader>
          <CardContent>
            <dl className="grid grid-cols-[140px_1fr] gap-y-2 text-[13px]">
              <dt className="text-muted">Version</dt><dd>PostgreSQL {db.postgres_version}</dd>
              <dt className="text-muted">Profile</dt><dd className="capitalize">{db.compute_profile} · {db.cpus} vCPU · {db.memory_mb} MB</dd>
              <dt className="text-muted">Storage</dt><dd>{formatBytes(db.storage_used_bytes)} used of {formatMB(db.allocated_storage_mb)}{db.storage_over_limit && <Badge variant="critical" className="ml-2">over limit</Badge>}</dd>
              <dt className="text-muted">Connections</dt><dd>{db.connections_current ?? "—"} / {db.max_connections}</dd>
              <dt className="text-muted">Created</dt><dd>{formatDateTime(db.created_at)}</dd>
              <dt className="text-muted">Measured</dt><dd>{formatDateTime(db.metrics_measured_at)}</dd>
            </dl>
          </CardContent>
        </Card>
      </div>
      <Card className="mt-4">
        <CardHeader><CardTitle>Provisioning jobs</CardTitle></CardHeader>
        <Table>
          <THead><TR><TH>Kind</TH><TH>Status</TH><TH>Step</TH><TH>Attempts</TH><TH>Error</TH><TH>Created</TH><TH>Completed</TH></TR></THead>
          <TBody>
            {d.jobs.map((j) => (
              <TR key={j.id}>
                <TD className="font-medium">{j.kind}</TD>
                <TD><Badge variant={j.status === "succeeded" ? "good" : j.status === "failed" ? "critical" : j.status === "cancelled" ? "neutral" : "accent"}>{j.status}</Badge></TD>
                <TD className="text-muted">{j.step ?? "—"}</TD>
                <TD className="tabular">{j.attempt_count}/{j.max_attempts}</TD>
                <TD className="max-w-xs text-xs text-critical">{j.error_code}{j.safe_error_message && <div className="text-muted">{j.safe_error_message}</div>}</TD>
                <TD className="whitespace-nowrap text-muted">{formatDateTime(j.created_at)}</TD>
                <TD className="whitespace-nowrap text-muted">{formatDateTime(j.completed_at)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </Card>
      <Card className="mt-4">
        <CardHeader><CardTitle>Backups</CardTitle><Link href="/admin/backups" className="text-xs text-muted hover:text-foreground">All backups</Link></CardHeader>
        {d.backups.length ? <BackupTable items={d.backups} /> : <p className="px-5 pb-5 text-[13px] text-subtle">No backups yet.</p>}
      </Card>
      <Card className="mt-4">
        <CardHeader><CardTitle>Events</CardTitle></CardHeader>
        <Table>
          <THead><TR><TH>Event</TH><TH>Message</TH><TH>Actor</TH><TH>When</TH></TR></THead>
          <TBody>
            {d.events.map((e) => (
              <TR key={e.id}><TD className="font-medium">{eventLabel(e)}</TD><TD className="text-muted">{e.message}</TD><TD className="text-muted">{e.actor_type}</TD><TD className="whitespace-nowrap text-muted">{formatDateTime(e.created_at)}</TD></TR>
            ))}
          </TBody>
        </Table>
      </Card>
      <MoveDialog open={moving} onOpenChange={setMoving} currentHost={d.placement.host} region={d.placement.region}
        loading={move.isPending} onConfirm={(target, reason) => move.mutate({ target, reason })} />
      {action && (
        <ReasonDialog open onOpenChange={(o) => !o && setAction(null)}
          title={{ retry: "Retry provisioning?", suspend: "Suspend this database?", unsuspend: "Lift the suspension?", backup: "Take a backup now?" }[action]}
          description={{ retry: "Queues a new provisioning job.", suspend: "Stops the instance and blocks the customer from starting it. Data is kept.", unsuspend: "Starts the instance again.", backup: "Queues an encrypted backup (counted as a scheduled backup, not against the customer's on-demand limit)." }[action]}
          destructive={action === "suspend"} confirmLabel="Confirm" loading={act.isPending} onConfirm={(reason) => act.mutate({ a: action, reason })} />
      )}
    </>
  );
}
