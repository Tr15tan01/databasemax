"use client";
import { AlertTriangle, ArchiveRestore, CheckCircle2, Clock, Loader2, ShieldCheck, Trash2 } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tooltip } from "@/components/ui/tooltip";
import type { Backup } from "@/lib/types";
import { formatBytes, formatDateTime, relativeTime } from "@/lib/utils";

export const KIND_LABEL: Record<Backup["kind"], string> = {
  scheduled: "Daily",
  manual: "On demand",
  final: "Final (before delete)",
  pre_clone: "Before copy",
  migration: "Before a server move",
};

export function BackupStatus({ b }: { b: Backup }) {
  if (b.status === "pending") return <Badge variant="neutral"><Clock /> Queued</Badge>;
  if (b.status === "running") return <Badge variant="accent"><Loader2 className="animate-spin" /> Running</Badge>;
  if (b.status === "failed")
    return (
      <Tooltip content={b.error_message ?? "The backup failed."}>
        <span><Badge variant="critical"><AlertTriangle /> Failed</Badge></span>
      </Tooltip>
    );
  if (b.status === "expired") return <Badge variant="outline">Expired</Badge>;
  if (b.verification_status === "passed")
    return (
      <Tooltip content={`Restored successfully in an isolated test instance ${relativeTime(b.verified_at)}.`}>
        <span><Badge variant="good"><ShieldCheck /> Verified</Badge></span>
      </Tooltip>
    );
  if (b.verification_status === "failed")
    return (
      <Tooltip content="A test restore of this backup didn't match. Operators have been alerted; prefer another backup.">
        <span><Badge variant="warning"><AlertTriangle /> Check failed</Badge></span>
      </Tooltip>
    );
  return <Badge variant="good"><CheckCircle2 /> Completed</Badge>;
}

function duration(ms: number | null | undefined) {
  if (ms == null) return "—";
  return ms < 1000 ? `${ms} ms` : ms < 60_000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms / 60_000)} min`;
}

export function BackupTable({
  items,
  showDatabase = false,
  onRestore,
  onDelete,
}: {
  items: Backup[];
  showDatabase?: boolean;
  onRestore?: (b: Backup) => void;
  onDelete?: (b: Backup) => void;
}) {
  return (
    <Table>
      <THead>
        <TR>
          <TH>Taken</TH>
          {showDatabase && <TH>Database</TH>}
          <TH>Type</TH>
          <TH>Status</TH>
          <TH className="text-right">Size</TH>
          <TH className="hidden text-right md:table-cell">Tables</TH>
          <TH className="hidden lg:table-cell">Duration</TH>
          <TH className="hidden md:table-cell">Kept until</TH>
          {onRestore && <TH className="sr-only">Actions</TH>}
        </TR>
      </THead>
      <TBody>
        {items.map((b) => (
          <TR key={b.id}>
            <TD className="whitespace-nowrap">
              <div className="font-medium">{formatDateTime(b.completed_at ?? b.created_at)}</div>
              <div className="text-xs text-subtle">{relativeTime(b.completed_at ?? b.created_at)}</div>
            </TD>
            {showDatabase && (
              <TD>
                {b.database_status === "deleted" ? (
                  <span className="text-muted">{b.database_name} <Badge variant="outline" className="ml-1">deleted</Badge></span>
                ) : (
                  <Link className="font-medium hover:underline" href={`/databases/${b.database_id}?tab=backups`}>{b.database_name}</Link>
                )}
              </TD>
            )}
            <TD className="whitespace-nowrap text-muted">{KIND_LABEL[b.kind]}</TD>
            <TD><BackupStatus b={b} /></TD>
            <TD className="tabular whitespace-nowrap text-right">{b.size_bytes != null ? formatBytes(b.size_bytes) : "—"}</TD>
            <TD className="tabular hidden text-right text-muted md:table-cell">{b.table_count ?? "—"}</TD>
            <TD className="tabular hidden text-muted lg:table-cell">{duration(b.duration_ms)}</TD>
            <TD className="hidden whitespace-nowrap text-muted md:table-cell">{b.expires_at ? formatDateTime(b.expires_at) : "—"}</TD>
            {onRestore && (
              <TD className="text-right">
                <div className="flex justify-end gap-1">
                  <Button size="sm" variant="secondary" disabled={!b.restorable} onClick={() => onRestore(b)} aria-label={`Restore backup from ${formatDateTime(b.completed_at ?? b.created_at)}`}>
                    <ArchiveRestore /> Restore
                  </Button>
                  {onDelete && (
                    <Button size="sm" variant="ghost" disabled={b.status === "pending" || b.status === "running" || b.status === "expired"}
                      onClick={() => onDelete(b)} aria-label={`Delete backup from ${formatDateTime(b.completed_at ?? b.created_at)}`}>
                      <Trash2 />
                    </Button>
                  )}
                </div>
              </TD>
            )}
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
