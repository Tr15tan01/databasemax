"use client";
import { Check, Copy, Download } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import type { SqlResultSet } from "@/lib/types";
import { cn, formatNumber } from "@/lib/utils";

import { toCsv, toJson } from "./sql-utils";

function download(name: string, type: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function ExportMenu({ rs }: { rs: SqlResultSet }) {
  const [copied, setCopied] = React.useState(false);
  const cols = rs.columns.map((c) => c.name);
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: the download options still work */
    }
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" aria-label="Export results">
          {copied ? <Check /> : <Download />} Export
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={() => copy(toCsv(cols, rs.rows))}><Copy /> Copy as CSV</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => copy(toJson(cols, rs.rows))}><Copy /> Copy as JSON</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => download("results.csv", "text/csv", toCsv(cols, rs.rows))}><Download /> Download CSV</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => download("results.json", "application/json", toJson(cols, rs.rows))}><Download /> Download JSON</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Cell({ value }: { value: string | null }) {
  if (value === null) return <span className="italic text-subtle">NULL</span>;
  const long = value.length > 120;
  return <span title={long ? value.slice(0, 2000) : undefined}>{long ? `${value.slice(0, 120)}…` : value}</span>;
}

export function ResultSetView({ rs, maxRows }: { rs: SqlResultSet; maxRows: number }) {
  if (rs.kind === "command") {
    return <p className="px-4 py-6 font-mono text-[13px] text-muted">{rs.command || "OK"}</p>;
  }
  if (rs.columns.length === 0) return <p className="px-4 py-6 text-[13px] text-muted">{rs.command || "No columns"}</p>;
  return (
    <div>
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2 text-xs text-muted">
        <span>
          {formatNumber(rs.row_count)} row{rs.row_count === 1 ? "" : "s"}
          {rs.truncated && (
            <span className="ml-2 rounded bg-warning-soft px-1.5 py-0.5 text-warning">
              showing {formatNumber(rs.rows.length)} (limit {formatNumber(maxRows)} rows / 3 MB; long values cut)
            </span>
          )}
        </span>
        <ExportMenu rs={rs} />
      </div>
      <div className="max-h-[480px] overflow-auto">
        <table className="w-full border-collapse font-mono text-xs">
          <thead className="sticky top-0 z-10 bg-surface-2">
            <tr>
              <th className="w-10 border-b border-border px-2 py-2 text-right font-normal text-subtle">#</th>
              {rs.columns.map((c, i) => (
                <th key={i} className="whitespace-nowrap border-b border-l border-border px-3 py-2 text-left font-medium">
                  {c.name} <span className="font-normal text-subtle">{c.type}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rs.rows.map((row, r) => (
              <tr key={r} className="hover:bg-surface-2">
                <td className="border-b border-border px-2 py-1.5 text-right text-subtle">{r + 1}</td>
                {row.map((v, c) => (
                  <td key={c} className={cn("max-w-[420px] truncate whitespace-nowrap border-b border-l border-border px-3 py-1.5")}>
                    <Cell value={v} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {rs.rows.length === 0 && <p className="px-4 py-6 text-center text-[13px] text-muted">No rows</p>}
      </div>
    </div>
  );
}

export function ResultsView({ results, maxRows }: { results: SqlResultSet[]; maxRows: number }) {
  const [active, setActive] = React.useState(results.length - 1);
  React.useEffect(() => setActive(results.length - 1), [results]);
  const rs = results[active];
  if (!rs) return null;
  return (
    <div>
      {results.length > 1 && (
        <div role="tablist" aria-label="Result sets" className="flex gap-1 overflow-x-auto border-b border-border px-2 pt-2">
          {results.map((r, i) => (
            <button
              key={i}
              role="tab"
              aria-selected={i === active}
              onClick={() => setActive(i)}
              className={cn(
                "whitespace-nowrap rounded-t-md px-3 py-1.5 text-xs",
                i === active ? "bg-surface-2 font-medium text-foreground" : "text-muted hover:text-foreground",
              )}
            >
              {i + 1}. {r.command || (r.kind === "rows" ? "Result" : "OK")}
            </button>
          ))}
        </div>
      )}
      <ResultSetView rs={rs} maxRows={maxRows} />
    </div>
  );
}
