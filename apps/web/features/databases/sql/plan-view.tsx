"use client";
import { AlertTriangle } from "lucide-react";

import { Tooltip } from "@/components/ui/tooltip";
import { formatNumber } from "@/lib/utils";

import { flattenPlan } from "./sql-utils";

function heat(share: number): string {
  if (share >= 0.5) return "bg-critical";
  if (share >= 0.2) return "bg-warning";
  return "bg-accent";
}

/** EXPLAIN (FORMAT JSON) as an indented tree with a bar showing where the time (ANALYZE) or the
 *  estimated cost goes, and a flag on nodes whose row estimate was off by 10x or more. */
export function PlanView({ plan }: { plan: unknown }) {
  const { nodes, planningMs, executionMs } = flattenPlan(plan);
  if (!nodes.length) return <p className="px-4 py-6 text-[13px] text-muted">No plan returned.</p>;
  const analyzed = nodes[0]!.actualMs !== undefined;
  const seqScans = nodes.filter((n) => n.nodeType === "Seq Scan" && n.share >= 0.2);
  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted">
        <span>{analyzed ? "Measured with EXPLAIN ANALYZE (ran read-only, rolled back)" : "Estimated plan (the query was not run)"}</span>
        {planningMs !== undefined && <span>Planning {planningMs.toFixed(2)} ms</span>}
        {executionMs !== undefined && <span>Execution {executionMs.toFixed(2)} ms</span>}
      </div>
      {seqScans.length > 0 && (
        <p className="flex gap-2 rounded-md bg-warning-soft px-3 py-2 text-xs text-warning">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            Most of the work is a full table scan of {seqScans.map((n) => n.relation).filter(Boolean).join(", ")}. An index on the
            filtered columns usually helps.
          </span>
        </p>
      )}
      <ol className="space-y-1">
        {nodes.map((n, i) => (
          <li key={i} className="rounded-md border border-border bg-surface px-3 py-2" style={{ marginLeft: Math.min(n.depth, 12) * 18 }}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <div className="min-w-0 text-[13px]">
                <span className="font-medium">{n.nodeType}</span>
                {n.relation && <span className="ml-1.5 font-mono text-xs text-muted">on {n.relation}</span>}
                {n.index && <span className="ml-1.5 font-mono text-xs text-muted">using {n.index}</span>}
                {n.misestimate && (
                  <Tooltip content="The planner's row estimate was far off; running ANALYZE on the table can fix stale statistics.">
                    <span className="ml-2 rounded bg-warning-soft px-1.5 py-0.5 text-[11px] text-warning">estimate off {n.misestimate}×</span>
                  </Tooltip>
                )}
              </div>
              <div className="flex shrink-0 gap-4 font-mono text-[11px] text-subtle">
                {analyzed ? (
                  <>
                    <span>{n.actualMs!.toFixed(2)} ms</span>
                    <span>{formatNumber(n.actualRows)} rows{n.loops && n.loops > 1 ? ` × ${formatNumber(n.loops)}` : ""}</span>
                  </>
                ) : (
                  <span>~{formatNumber(n.planRows)} rows</span>
                )}
                <span>cost {n.totalCost.toFixed(1)}</span>
              </div>
            </div>
            {n.detail && <div className="mt-0.5 truncate font-mono text-[11px] text-subtle" title={n.detail}>{n.detail}</div>}
            <div className="mt-1.5 flex items-center gap-2">
              <div className="h-1 flex-1 overflow-hidden rounded-full bg-surface-3" aria-hidden>
                <div className={`h-full rounded-full ${heat(n.share)}`} style={{ width: `${Math.max(n.share * 100, 1)}%` }} />
              </div>
              <span className="w-10 text-right text-[11px] tabular-nums text-subtle">{Math.round(n.share * 100)}%</span>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
