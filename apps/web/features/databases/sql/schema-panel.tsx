"use client";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Eye, KeyRound, RefreshCcw, Search, Table2 } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage } from "@/lib/api";
import type { DbSchema, SchemaTable } from "@/lib/types";
import { cn, formatBytes, formatNumber } from "@/lib/utils";

import { quoteIdent } from "./sql-utils";

function TableItem({ t, onInsert }: { t: SchemaTable; onInsert: (sql: string) => void }) {
  const [open, setOpen] = React.useState(false);
  const name = `${t.schema_name === "public" ? "" : `${quoteIdent(t.schema_name)}.`}${quoteIdent(t.name)}`;
  const Icon = t.kind.includes("view") ? Eye : Table2;
  return (
    <li>
      <div className="group flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-surface-2">
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label={`Columns of ${t.name}`} className="text-subtle">
          <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
        </button>
        <Icon className="size-3.5 shrink-0 text-subtle" aria-hidden />
        <button
          type="button"
          className="min-w-0 flex-1 truncate text-left font-mono text-xs"
          title={`Insert SELECT for ${t.schema_name}.${t.name}`}
          onClick={() => onInsert(`SELECT * FROM ${name} LIMIT 100;`)}
        >
          {t.schema_name !== "public" && <span className="text-subtle">{t.schema_name}.</span>}
          {t.name}
        </button>
        <span className="hidden shrink-0 text-[10px] text-subtle group-hover:inline">
          {t.rows_estimate !== null && t.rows_estimate >= 0 ? `~${formatNumber(t.rows_estimate)}` : ""}
          {t.total_bytes ? ` · ${formatBytes(t.total_bytes, 0)}` : ""}
        </span>
      </div>
      {open && (
        <ul className="mb-1 ml-7 border-l border-border pl-2">
          {t.columns.map((c) => (
            <li key={c.name} className="flex items-center gap-1.5 py-0.5 font-mono text-[11px]">
              {c.primary_key ? <KeyRound className="size-3 text-warning" aria-label="Primary key" /> : <span className="w-3" />}
              <button type="button" className="truncate hover:underline" onClick={() => onInsert(quoteIdent(c.name))} title="Insert column name">
                {c.name}
              </button>
              <span className="truncate text-subtle">{c.type}{c.nullable ? "" : " not null"}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function SchemaPanel({ databaseId, onInsert, refreshKey }: { databaseId: string; onInsert: (sql: string) => void; refreshKey: number }) {
  const [filter, setFilter] = React.useState("");
  const q = useQuery({
    queryKey: ["db-schema", databaseId, refreshKey],
    queryFn: () => api<DbSchema>(`/databases/${databaseId}/schema`),
    staleTime: 60_000,
    retry: false,
  });
  const tables = (q.data?.tables ?? []).filter((t) => `${t.schema_name}.${t.name}`.toLowerCase().includes(filter.toLowerCase()));
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 px-3 pb-2 pt-3">
        <span className="text-xs font-medium text-muted">Tables</span>
        <Button variant="ghost" size="icon-sm" onClick={() => q.refetch()} aria-label="Refresh tables" disabled={q.isFetching}>
          <RefreshCcw className={cn(q.isFetching && "animate-spin")} />
        </Button>
      </div>
      <div className="relative px-3 pb-2">
        <Search className="pointer-events-none absolute left-5 top-2.5 size-3.5 text-subtle" aria-hidden />
        <Input aria-label="Filter tables" placeholder="Filter" className="h-8 pl-7 text-xs" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-3">
        {q.isLoading && <div className="space-y-2 px-1.5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-5" />)}</div>}
        {q.isError && <p className="px-2 text-xs text-critical">{errorMessage(q.error)}</p>}
        {q.data && tables.length === 0 && (
          <p className="px-2 text-xs text-subtle">{filter ? "No matching tables." : "No tables yet. Create one with CREATE TABLE."}</p>
        )}
        <ul>{tables.map((t) => <TableItem key={`${t.schema_name}.${t.name}`} t={t} onInsert={onInsert} />)}</ul>
        {q.data?.truncated && <p className="px-2 pt-2 text-[11px] text-subtle">Showing the first 500 tables.</p>}
      </div>
    </div>
  );
}
