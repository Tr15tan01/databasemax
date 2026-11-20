"use client";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArchiveRestore, DatabaseBackup, HardDrive, ShieldCheck } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { NativeSelect } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { Stat } from "@/components/ui/stat";
import { BackupTable } from "@/features/backups/backup-table";
import { useDeleteBackup } from "@/features/backups/delete-backup";
import { RestoreDialog } from "@/features/backups/restore-dialog";
import { api } from "@/lib/api";
import type { Backup, DashboardSummary, Page } from "@/lib/types";
import { formatBytes, formatDateTime, relativeTime } from "@/lib/utils";

const PAGE_SIZE = 25;
type Filter = "restorable" | "in_progress" | "failed" | "all";

export default function BackupsPage() {
  const [page, setPage] = React.useState(1);
  const [status, setStatus] = React.useState<Filter>("restorable");
  const [restoring, setRestoring] = React.useState<Backup | null>(null);
  const [requestDelete, deleteDialog] = useDeleteBackup();
  React.useEffect(() => setPage(1), [status]);
  const q = useQuery({
    queryKey: ["backups", "all", status, page],
    queryFn: () => api<Page<Backup>>("/backups", { query: { status, page, page_size: PAGE_SIZE } }),
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      ((query.state.data as Page<Backup> | undefined)?.items ?? []).some((b) => b.status === "pending" || b.status === "running") ? 2000 : 30_000,
  });
  const summary = useQuery({ queryKey: ["dashboard"], queryFn: () => api<DashboardSummary>("/dashboard/summary") });
  const b = summary.data?.backups;
  return (
    <>
      <PageHeader
        title="Backups"
        description="Encrypted daily backups of every running database, plus on-demand and final backups. Restores always create a new database."
      />
      <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Restorable backups" icon={DatabaseBackup} value={b ? b.count : <Skeleton className="h-7 w-12" />} sub={b ? `kept ${b.retention_days} days on your plan` : undefined} />
        <Stat label="Stored" icon={HardDrive} hint="Encrypted, compressed size." value={b ? formatBytes(b.stored_bytes) : <Skeleton className="h-7 w-20" />} />
        <Stat label="Most recent" icon={ShieldCheck} value={b ? (b.last_completed_at ? relativeTime(b.last_completed_at) : "None yet") : <Skeleton className="h-7 w-24" />}
          sub={b?.last_completed_at ? formatDateTime(b.last_completed_at) : undefined} />
        <Stat label="Failed (24 h)" icon={ArchiveRestore} value={b ? b.failed_24h : <Skeleton className="h-7 w-12" />}
          sub={b && b.failed_24h > 0 ? "we retry at the next scheduled run" : "all good"} />
      </div>
      <div className="mb-4 flex flex-col gap-2 sm:flex-row">
        <NativeSelect aria-label="Filter backups" className="sm:w-52" value={status} onChange={(e) => setStatus(e.target.value as Filter)}>
          <option value="restorable">Restorable</option>
          <option value="in_progress">In progress</option>
          <option value="failed">Failed</option>
          <option value="all">All (incl. expired)</option>
        </NativeSelect>
      </div>
      <Card className="p-2 sm:p-4">
        {q.isLoading ? (
          <div className="space-y-2 p-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        ) : !q.data?.items.length ? (
          <EmptyState icon={DatabaseBackup} title={status === "restorable" ? "No restorable backups yet" : "Nothing here"}
            description={<>Running databases are backed up daily. You can also take one from a database&apos;s <span className="font-medium">Backups</span> tab. <Link className="text-accent hover:underline" href="/databases">Go to databases</Link></>} />
        ) : (
          <>
            <BackupTable items={q.data.items} showDatabase onRestore={setRestoring} onDelete={requestDelete} />
            <Pagination page={page} pageSize={PAGE_SIZE} total={q.data.total} onPage={setPage} />
          </>
        )}
      </Card>
      {deleteDialog}
      {restoring && (
        <RestoreDialog open onOpenChange={(o) => !o && setRestoring(null)} backup={restoring}
          source={{ id: restoring.database_id, name: restoring.database_name ?? "database", storage_mb: restoring.source_storage_mb }} />
      )}
    </>
  );
}
