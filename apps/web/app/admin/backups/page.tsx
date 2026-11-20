"use client";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Clock, DatabaseBackup, HardDrive, ShieldCheck, Timer } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Input, NativeSelect } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Pagination } from "@/components/ui/pagination";
import { Meter } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Stat } from "@/components/ui/stat";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tooltip } from "@/components/ui/tooltip";
import type { BackupOverview } from "@/features/admin/backup-types";
import { ReasonDialog } from "@/features/admin/reason-dialog";
import { KIND_LABEL } from "@/features/backups/backup-table";
import { api, errorMessage } from "@/lib/api";
import { formatBytes, formatDateTime, relativeTime } from "@/lib/utils";

interface Row {
  id: string; database_id: string; database_name: string; database_status: string; owner_email: string;
  kind: keyof typeof KIND_LABEL; status: string; size_bytes: number | null; table_count: number | null; store: string | null;
  encryption_key_id: string | null; created_at: string; completed_at: string | null; duration_ms: number | null;
  expires_at: string | null; verified_at: string | null; verification_status: "passed" | "failed" | null;
  verification_error: string | null; error_message: string | null;
}
interface Resp { overview: BackupOverview; items: Row[]; total: number }

const PAGE = 50;

function statusBadge(r: Row) {
  const v = { completed: "good", verified: "good", failed: "critical", running: "accent", pending: "neutral", expired: "outline" } as const;
  const badge = <Badge variant={v[r.status as keyof typeof v] ?? "neutral"}>{r.status}</Badge>;
  return r.error_message ? <Tooltip content={r.error_message}><span>{badge}</span></Tooltip> : badge;
}

export default function AdminBackups() {
  const qc = useQueryClient();
  const [status, setStatus] = React.useState("");
  const [kind, setKind] = React.useState("");
  const [verification, setVerification] = React.useState("");
  const [search, setSearch] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [verifying, setVerifying] = React.useState<Row | null>(null);
  React.useEffect(() => setPage(1), [status, kind, verification, search]);
  const q = useQuery({
    queryKey: ["admin", "backups", status, kind, verification, search, page],
    queryFn: () => api<Resp>("/admin/backups", { query: { status: status || undefined, kind: kind || undefined, verification: verification || undefined, q: search || undefined, page, page_size: PAGE } }),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
  const verify = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => api(`/admin/backups/${id}/verify`, { method: "POST", body: { reason } }),
    onSuccess: () => { toast.success("Restore verification queued"); setVerifying(null); qc.invalidateQueries({ queryKey: ["admin", "backups"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const o = q.data?.overview;
  const coverage = o && o.ready_databases ? (o.ready_databases_with_recent_backup / o.ready_databases) * 100 : 100;
  return (
    <>
      <PageHeader title="Backups" description="Encrypted logical backups taken by the provisioner, their retention and restore verification. Contents are never visible here." />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Stat label="Coverage" icon={ShieldCheck} hint="Running databases with a completed backup in the last 26 hours."
          value={o ? `${o.ready_databases_with_recent_backup}/${o.ready_databases}` : <Skeleton className="h-7 w-16" />}>
          {o && <Meter className="mt-3" value={coverage} label="Backup coverage" />}
        </Stat>
        <Stat label="Stored" icon={HardDrive} value={o ? formatBytes(o.stored_bytes) : <Skeleton className="h-7 w-16" />} sub="restorable, encrypted" />
        <Stat label="Last 24 hours" icon={DatabaseBackup} value={o ? (o.last_24h.completed ?? 0) + (o.last_24h.verified ?? 0) : <Skeleton className="h-7 w-10" />}
          sub={o ? <span className={(o.last_24h.failed ?? 0) > 0 ? "text-critical" : undefined}>{o.last_24h.failed ?? 0} failed · {(o.last_24h.pending ?? 0) + (o.last_24h.running ?? 0)} in progress</span> : undefined} />
        <Stat label="Restore checks (30 d)" icon={CheckCircle2} value={o ? o.verification_30d.passed : <Skeleton className="h-7 w-10" />}
          sub={o ? <span className={o.verification_30d.failed ? "text-critical" : undefined}>{o.verification_30d.failed} failed</span> : undefined} />
        <Stat label="Avg duration (7 d)" icon={Timer} value={o ? (o.avg_duration_ms_7d == null ? "—" : `${(o.avg_duration_ms_7d / 1000).toFixed(1)} s`) : <Skeleton className="h-7 w-16" />} />
      </div>
      {o && (
        <div className="mt-4 flex flex-wrap gap-2 text-xs">
          {Object.entries(o.by_status).sort().map(([k, v]) => <Badge key={k} variant="outline">{k}: <span className="tabular">{v}</span></Badge>)}
        </div>
      )}
      <div className="mb-4 mt-6 flex flex-col gap-2 lg:flex-row">
        <Input aria-label="Search" placeholder="Search database, owner email or backup id" className="lg:w-80" value={search} onChange={(e) => setSearch(e.target.value)} />
        <NativeSelect aria-label="Status" className="lg:w-40" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>{["pending", "running", "completed", "verified", "failed", "expired"].map((s) => <option key={s} value={s}>{s}</option>)}
        </NativeSelect>
        <NativeSelect aria-label="Type" className="lg:w-44" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">Any type</option>{Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </NativeSelect>
        <NativeSelect aria-label="Verification" className="lg:w-44" value={verification} onChange={(e) => setVerification(e.target.value)}>
          <option value="">Any verification</option><option value="passed">Passed</option><option value="failed">Failed</option><option value="none">Not verified</option>
        </NativeSelect>
      </div>
      <Card>
        {!q.data ? <div className="p-5"><Skeleton className="h-40" /></div> : q.data.items.length === 0 ? (
          <p className="p-10 text-center text-[13px] text-subtle">No backups match.</p>
        ) : (
          <Table>
            <THead><TR><TH>Taken</TH><TH>Database</TH><TH>Type</TH><TH>Status</TH><TH className="text-right">Size</TH><TH className="text-right">Tables</TH><TH>Verification</TH><TH>Store · key</TH><TH>Expires</TH><TH className="sr-only">Actions</TH></TR></THead>
            <TBody>
              {q.data.items.map((r) => (
                <TR key={r.id}>
                  <TD className="whitespace-nowrap">{formatDateTime(r.completed_at ?? r.created_at)}
                    <div className="text-xs text-subtle">{r.duration_ms != null ? `${(r.duration_ms / 1000).toFixed(1)} s` : relativeTime(r.created_at)}</div></TD>
                  <TD><Link href={`/admin/databases/${r.database_id}`} className="font-medium hover:underline">{r.database_name}</Link>
                    {r.database_status === "deleted" && <Badge variant="outline" className="ml-1">deleted</Badge>}
                    <div className="max-w-56 truncate text-xs text-subtle" title={r.owner_email}>{r.owner_email}</div></TD>
                  <TD className="whitespace-nowrap text-muted">{KIND_LABEL[r.kind] ?? r.kind}</TD>
                  <TD>{statusBadge(r)}</TD>
                  <TD className="tabular whitespace-nowrap text-right">{r.size_bytes != null ? formatBytes(r.size_bytes) : "—"}</TD>
                  <TD className="tabular text-right text-muted">{r.table_count ?? "—"}</TD>
                  <TD>
                    {r.verification_status === "passed" ? <Badge variant="good"><ShieldCheck /> passed</Badge>
                      : r.verification_status === "failed" ? <Tooltip content={r.verification_error ?? ""}><span><Badge variant="critical"><AlertTriangle /> failed</Badge></span></Tooltip>
                        : r.verification_error ? <Tooltip content={r.verification_error}><span><Badge variant="warning"><Clock /> not run</Badge></span></Tooltip>
                          : <span className="text-xs text-subtle">—</span>}
                    {r.verified_at && <div className="text-xs text-subtle">{relativeTime(r.verified_at)}</div>}
                  </TD>
                  <TD className="whitespace-nowrap font-mono text-[11px] text-subtle">{r.store ?? "—"} · {r.encryption_key_id ?? "—"}</TD>
                  <TD className="whitespace-nowrap text-muted">{r.expires_at ? relativeTime(r.expires_at) : "—"}</TD>
                  <TD className="text-right">
                    <Button size="sm" variant="secondary" disabled={!["completed", "verified"].includes(r.status)} onClick={() => setVerifying(r)}>Verify</Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Card>
      {q.data && <Pagination page={page} pageSize={PAGE} total={q.data.total} onPage={setPage} />}
      <ReasonDialog open={!!verifying} onOpenChange={(o) => !o && setVerifying(null)} title="Run a restore verification"
        description="Restores this backup into an isolated scratch instance, compares the table count and destroys the instance. Uses data-plane capacity while it runs."
        confirmLabel="Queue verification" loading={verify.isPending} onConfirm={(reason) => verifying && verify.mutate({ id: verifying.id, reason })} />
    </>
  );
}
