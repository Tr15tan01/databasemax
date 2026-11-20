"use client";
import { useQuery } from "@tanstack/react-query";
import { AlertOctagon, AlertTriangle, CheckCircle2, HardDrive, Info, RefreshCcw, SquareTerminal, Stethoscope } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CopyButton } from "@/components/ui/copy-button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Meter } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { Database, Insight, Insights } from "@/lib/types";
import { cn, formatBytes, formatDateTime, pct, relativeTime } from "@/lib/utils";

const SEVERITY = {
  critical: { icon: AlertOctagon, badge: "critical", label: "Critical", tone: "text-critical" },
  warning: { icon: AlertTriangle, badge: "warning", label: "Warning", tone: "text-warning" },
  info: { icon: Info, badge: "accent", label: "Tip", tone: "text-accent" },
  ok: { icon: CheckCircle2, badge: "good", label: "Healthy", tone: "text-good" },
} as const;

function ScoreRing({ score, grade }: { score: number; grade: string }) {
  const r = 42;
  const c = 2 * Math.PI * r;
  const color = score >= 90 ? "var(--good)" : score >= 75 ? "var(--accent)" : score >= 50 ? "var(--warning)" : "var(--critical)";
  return (
    <div className="relative size-32 shrink-0">
      <svg viewBox="0 0 100 100" className="size-full -rotate-90" role="img" aria-label={`Health score ${score} out of 100, ${grade}`}>
        <circle cx="50" cy="50" r={r} fill="none" stroke="var(--surface-3)" strokeWidth="8" />
        <circle cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="8" strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - score / 100)} className="transition-[stroke-dashoffset] duration-700" />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-3xl font-semibold tabular-nums">{score}</span>
        <span className="text-xs capitalize text-muted">{grade}</span>
      </div>
    </div>
  );
}

function Finding({ f, onOpenSql }: { f: Insight; onOpenSql: (sql: string) => void }) {
  const s = SEVERITY[f.severity];
  const Icon = s.icon;
  return (
    <li className="flex gap-3 border-b border-border py-4 last:border-0">
      <Icon className={cn("mt-0.5 size-4 shrink-0", s.tone)} aria-hidden />
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-medium">{f.title}</span>
          <Badge variant={s.badge}>{s.label}</Badge>
          <Badge variant="outline" className="capitalize">{f.category}</Badge>
        </div>
        <p className="text-[13px] text-muted">{f.detail}</p>
        {f.suggestion && <p className="text-[13px]"><span className="text-muted">Suggestion: </span>{f.suggestion}</p>}
        {f.sql && (
          <div className="flex items-center gap-1 rounded-md bg-surface-2 py-1 pl-3 pr-1">
            <code className="min-w-0 flex-1 truncate font-mono text-xs" title={f.sql}>{f.sql}</code>
            <CopyButton value={f.sql} label="Copy SQL" />
            <Button variant="ghost" size="sm" onClick={() => onOpenSql(f.sql!)}><SquareTerminal /> Open in editor</Button>
          </div>
        )}
      </div>
    </li>
  );
}

export function InsightsTab({ database: d }: { database: Database }) {
  const router = useRouter();
  const pathname = usePathname();
  const q = useQuery({
    queryKey: ["db-insights", d.id],
    queryFn: () => api<Insights>(`/databases/${d.id}/insights`),
    enabled: d.status === "ready",
    staleTime: 60_000,
    retry: false,
  });
  const openSql = React.useCallback(
    (sql: string) => {
      try {
        sessionStorage.setItem(`dmx:sql-draft:${d.id}`, `-- Suggested by Insights. Review before running.\n${sql}\n`);
      } catch {
        /* no storage: the SQL can still be copied */
      }
      router.replace(`${pathname}?tab=sql`, { scroll: false });
    },
    [d.id, pathname, router],
  );

  if (d.status !== "ready") return <EmptyState icon={Stethoscope} title="Insights need a running database" description="Start the database to run a health check." />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} title="Couldn't run the health check" />;
  const data = q.data;
  if (!data) return <div className="grid gap-4 lg:grid-cols-[1fr_1.6fr]"><Skeleton className="h-56" /><Skeleton className="h-56" /></div>;
  const f = data.forecast;
  const counts = { critical: 0, warning: 0, info: 0, ok: 0 };
  data.findings.forEach((x) => counts[x.severity]++);
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
        <Card>
          <CardContent className="flex flex-col items-center gap-6 pt-6 sm:flex-row">
            <ScoreRing score={data.score} grade={data.grade} />
            <div className="space-y-2 text-[13px]">
              <h3 className="text-base font-semibold">Database health</h3>
              <p className="text-muted">
                {counts.critical + counts.warning === 0
                  ? "No urgent problems. Tips below can still make it faster or leaner."
                  : `${counts.critical} critical and ${counts.warning} warning finding${counts.critical + counts.warning === 1 ? "" : "s"} to look at.`}
              </p>
              <p className="text-xs text-subtle">
                Checked {relativeTime(data.generated_at)} using PostgreSQL&apos;s statistics
                {data.stats_since ? ` collected since ${formatDateTime(data.stats_since)}` : ""}. Only metadata is read — never your rows.
              </p>
              <Button variant="secondary" size="sm" onClick={() => q.refetch()} loading={q.isFetching}><RefreshCcw /> Run again</Button>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Storage forecast</CardTitle>
              <CardDescription>Growth trend over the last two weeks</CardDescription>
            </div>
            <HardDrive className="size-4 text-subtle" />
          </CardHeader>
          <CardContent className="space-y-3 text-[13px]">
            <div className="flex items-baseline justify-between">
              <span className="text-2xl font-semibold tabular-nums">{formatBytes(f.used_bytes)}</span>
              <span className="text-muted">of {formatBytes(f.limit_bytes, 0)}</span>
            </div>
            <Meter value={pct(f.used_bytes ?? 0, f.limit_bytes)} label="Storage used" />
            <p className="text-muted">
              {f.growth_bytes_per_day === null
                ? "Not enough history yet to forecast growth (needs about six hours of samples)."
                : f.growth_bytes_per_day <= 0
                  ? "Storage isn't growing."
                  : `Growing about ${formatBytes(f.growth_bytes_per_day)} per day.`}
            </p>
            {f.days_until_full !== null && (
              <p className={cn("font-medium", f.days_until_full < 7 ? "text-critical" : f.days_until_full < 30 ? "text-warning" : "text-foreground")}>
                {f.days_until_full <= 0 ? "Storage is full." : `Full in about ${Math.round(f.days_until_full)} days at this rate.`}
              </p>
            )}
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Findings</CardTitle>
            <CardDescription>Suggested SQL is never run for you — review it, then run it in the SQL editor.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <ul>{data.findings.map((x) => <Finding key={x.id} f={x} onOpenSql={openSql} />)}</ul>
        </CardContent>
      </Card>
    </div>
  );
}
