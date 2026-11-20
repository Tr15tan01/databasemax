"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArchiveRestore, CalendarClock, Copy, DatabaseBackup, HardDrive, History, Lock, Plus } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CodeBlock } from "@/components/ui/code-block";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { Stat } from "@/components/ui/stat";
import { BackupTable } from "@/features/backups/backup-table";
import { useDeleteBackup } from "@/features/backups/delete-backup";
import { RestoreDialog } from "@/features/backups/restore-dialog";
import { api, errorMessage } from "@/lib/api";
import type { Backup, BackupSummary, Database, Page } from "@/lib/types";
import { formatBytes, formatDateTime, relativeTime } from "@/lib/utils";

const PAGE_SIZE = 20;

export function BackupsTab({ database: d }: { database: Database }) {
  const qc = useQueryClient();
  const [page, setPage] = React.useState(1);
  const [restoring, setRestoring] = React.useState<Backup | null | undefined>(undefined);
  const [requestDelete, deleteDialog] = useDeleteBackup();
  const list = useQuery({
    queryKey: ["backups", d.id, page],
    queryFn: () => api<Page<Backup>>(`/databases/${d.id}/backups`, { query: { page, page_size: PAGE_SIZE } }),
    refetchInterval: (q) => {
      const items = (q.state.data as Page<Backup> | undefined)?.items ?? [];
      return items.some((b) => b.status === "pending" || b.status === "running") ? 2000 : 30_000;
    },
  });
  const summary = useQuery({
    queryKey: ["backups", d.id, "summary"],
    queryFn: () => api<BackupSummary>(`/databases/${d.id}/backups/summary`),
    refetchInterval: 30_000,
  });
  const inProgress = (list.data?.items ?? []).some((b) => b.status === "pending" || b.status === "running");
  // Keep the summary cards in step with the list (a backup finishing changes both).
  const listSignature = (list.data?.items ?? []).map((b) => `${b.id}:${b.status}`).join(",");
  const refetchSummary = summary.refetch;
  React.useEffect(() => {
    if (listSignature) void refetchSummary();
  }, [listSignature, refetchSummary]);
  const take = useMutation({
    mutationFn: () => api<Backup>(`/databases/${d.id}/backups`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Backup started", { description: "It's encrypted as it's written. You'll see it here when it completes." });
      qc.invalidateQueries({ queryKey: ["backups", d.id] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const s = summary.data;
  const quotaLeft = s ? s.manual_backups_per_day - s.manual_backups_today : 0;
  const running = d.status === "ready";
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Schedule" icon={CalendarClock} value={s ? "Daily" : <Skeleton className="h-7 w-20" />}
          sub={s?.next_backup_due_at ? `next one due ${relativeTime(s.next_backup_due_at)}` : running ? "automatic" : "paused while stopped"} />
        <Stat label="Retention" icon={History} value={s ? `${s.retention_days} days` : <Skeleton className="h-7 w-20" />} sub="older backups are deleted automatically" />
        <Stat label="Last backup" icon={DatabaseBackup} value={s ? (s.last_backup_at ? relativeTime(s.last_backup_at) : "None yet") : <Skeleton className="h-7 w-24" />}
          sub={s?.last_backup_at ? formatDateTime(s.last_backup_at) : running ? "the first one runs within a day" : undefined} />
        <Stat label="Stored" icon={HardDrive} hint="Encrypted, compressed size of restorable backups."
          value={s ? formatBytes(s.total_size_bytes) : <Skeleton className="h-7 w-20" />} sub={s ? `${s.count} restorable backup${s.count === 1 ? "" : "s"}` : undefined} />
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Backups</CardTitle>
            <CardDescription>
              Logical snapshots (pg_dump) encrypted with AES-256-GCM before they&apos;re stored. Restoring always creates a new database — nothing is overwritten.
            </CardDescription>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button size="sm" variant="secondary" disabled={!running} onClick={() => setRestoring(null)}><Copy /> Copy current data</Button>
            <Button size="sm" loading={take.isPending} disabled={!running || inProgress || (s ? quotaLeft <= 0 : true)} onClick={() => take.mutate()}>
              <Plus /> Back up now
            </Button>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          {s && (
            <p className="mb-3 text-xs text-subtle">
              {running
                ? inProgress
                  ? "A backup is in progress."
                  : `${Math.max(quotaLeft, 0)} of ${s.manual_backups_per_day} on-demand backup${s.manual_backups_per_day === 1 ? "" : "s"} left in the last 24 hours on your plan.`
                : "Start the database to take backups or copy its current data. Existing backups can still be restored."}
            </p>
          )}
          {list.isLoading ? (
            <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
          ) : list.isError ? (
            <ErrorState error={list.error} onRetry={() => list.refetch()} />
          ) : !list.data?.items.length ? (
            <EmptyState icon={ArchiveRestore} title="No backups yet"
              description={running ? "Daily backups start automatically. Take one now if you're about to make a risky change." : "Backups run while the database is running."} />
          ) : (
            <>
              <BackupTable items={list.data.items} onRestore={(b) => setRestoring(b)} onDelete={requestDelete} />
              <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={setPage} />
            </>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <h3 className="flex items-center gap-2 text-sm font-semibold"><Lock className="size-4 text-subtle" aria-hidden /> How backups are protected</h3>
          <ul className="mt-2 space-y-1.5 text-[13px] text-muted">
            <li>Encrypted while streaming out of your instance; plaintext never touches disk and never passes through our web or API servers.</li>
            <li>Every chunk is authenticated, so a modified or truncated backup is rejected before anything is restored.</li>
            <li>Backups are regularly test-restored into isolated instances that customers can&apos;t reach, then destroyed.</li>
            <li>Point-in-time recovery isn&apos;t offered: you can restore to the moment a backup was taken.</li>
          </ul>
        </Card>
        <Card className="p-5">
          <h3 className="text-sm font-semibold">Keep your own copy too</h3>
          <p className="mb-3 mt-1 text-[13px] text-muted">Use the connection URL from the Connection tab. Custom-format dumps restore with pg_restore.</p>
          <CodeBlock lang="bash" wrap code={`pg_dump "$DATABASE_URL" --format=custom --no-owner --file=${d.database_name}.dump
pg_restore --no-owner --dbname "$OTHER_DATABASE_URL" ${d.database_name}.dump`} />
        </Card>
      </div>

      {deleteDialog}
      <RestoreDialog open={restoring !== undefined} onOpenChange={(o) => !o && setRestoring(undefined)}
        source={{ id: d.id, name: d.name, project_id: d.project_id, environment: d.environment, storage_mb: d.allocated_storage_mb }} backup={restoring ?? null} />
    </div>
  );
}
