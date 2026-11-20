"use client";
import { ArrowDown, ArrowUp } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Meter } from "@/components/ui/progress";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tooltip } from "@/components/ui/tooltip";
import type { Database } from "@/lib/types";
import { formatBytes, formatDate, formatMB, pct, relativeTime } from "@/lib/utils";

export type SortKey = "created_at" | "name" | "status" | "storage" | "last_activity";

function SortHeader({ label, k, sort, order, onSort }: { label: string; k: SortKey; sort?: SortKey; order?: "asc" | "desc"; onSort?: (k: SortKey) => void }) {
  if (!onSort) return <TH>{label}</TH>;
  const active = sort === k;
  return (
    <TH aria-sort={active ? (order === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" onClick={() => onSort(k)} className="inline-flex items-center gap-1 hover:text-foreground">
        {label} {active && (order === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
      </button>
    </TH>
  );
}

export function DatabaseTable({ items, showProject = true, sort, order, onSort }: { items: Database[]; showProject?: boolean; sort?: SortKey; order?: "asc" | "desc"; onSort?: (k: SortKey) => void }) {
  const router = useRouter();
  return (
    <Table>
      <THead>
        <TR>
          <SortHeader label="Name" k="name" sort={sort} order={order} onSort={onSort} />
          <SortHeader label="Status" k="status" sort={sort} order={order} onSort={onSort} />
          <TH>Version</TH>
          <TH>Size</TH>
          <SortHeader label="Storage" k="storage" sort={sort} order={order} onSort={onSort} />
          <TH>Connections</TH>
          <TH className="hidden xl:table-cell">Region</TH>
          <SortHeader label="Last activity" k="last_activity" sort={sort} order={order} onSort={onSort} />
          <SortHeader label="Created" k="created_at" sort={sort} order={order} onSort={onSort} />
        </TR>
      </THead>
      <TBody>
        {items.map((d) => {
          const used = d.storage_used_bytes ?? 0;
          const storagePct = pct(used, d.allocated_storage_mb * 1024 * 1024);
          return (
            <TR key={d.id} className="cursor-pointer hover:bg-surface-2/60" onClick={() => router.push(`/databases/${d.id}`)}>
              <TD>
                <Link href={`/databases/${d.id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>{d.name}</Link>
                {showProject && <div className="text-xs text-subtle">{d.project_name} · {d.environment}</div>}
              </TD>
              <TD><StatusBadge status={d.status} /></TD>
              <TD className="tabular text-muted">{d.postgres_version}</TD>
              <TD className="capitalize text-muted">{d.compute_profile}</TD>
              <TD className="min-w-36">
                <div className="mb-1 flex justify-between gap-2 text-xs tabular">
                  <span>{d.storage_used_bytes === null ? "—" : formatBytes(used)}</span>
                  <span className="text-subtle">{formatMB(d.allocated_storage_mb)}</span>
                </div>
                <Meter value={storagePct} label={`Storage used for ${d.name}`} />
              </TD>
              <TD className="tabular text-muted">{d.connections_current ?? "—"} / {d.max_connections}</TD>
              <TD className="hidden text-muted xl:table-cell"><Tooltip content={d.host}><span>{d.region_label ?? d.region}</span></Tooltip></TD>
              <TD className="whitespace-nowrap text-muted">{relativeTime(d.last_activity_at)}</TD>
              <TD className="whitespace-nowrap text-muted">{formatDate(d.created_at)}</TD>
            </TR>
          );
        })}
      </TBody>
    </Table>
  );
}
