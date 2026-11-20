"use client";
import { LineChart as LineIcon } from "lucide-react";
import * as React from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { cn } from "@/lib/utils";

export interface Point {
  t: string;
  v: number;
}

const fmtDay = (t: string) => new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(new Date(t));
const fmtHour = (t: string) => new Intl.DateTimeFormat("en-US", { hour: "numeric" }).format(new Date(t));
const fmtFull = (t: string, withTime: boolean) =>
  new Intl.DateTimeFormat("en-US", withTime ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" } : { dateStyle: "medium" }).format(new Date(t));

function ChartTooltip({
  active,
  payload,
  label,
  format,
  name,
  withTime,
}: {
  active?: boolean;
  payload?: { value: number }[];
  label?: string;
  format: (v: number) => string;
  name: string;
  withTime: boolean;
}) {
  if (!active || !payload?.length || !label) return null;
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2 text-xs shadow-[var(--shadow-pop)]">
      <div className="text-subtle">{fmtFull(label, withTime)}</div>
      <div className="mt-0.5 flex items-center gap-2">
        <span className="size-2 rounded-full bg-[var(--chart-1)]" aria-hidden />
        <span className="text-muted">{name}</span>
        <span className="ml-auto font-medium tabular text-foreground">{format(payload[0]!.value)}</span>
      </div>
    </div>
  );
}

function Empty({ message, height }: { message: string; height: number }) {
  return (
    <div style={{ height }} className="flex flex-col items-center justify-center gap-2 rounded-md border border-dashed border-border text-center">
      <LineIcon className="size-4 text-subtle" aria-hidden />
      <p className="max-w-xs text-xs text-subtle">{message}</p>
    </div>
  );
}

/** Single-series time chart (area or bar). Title outside names the series, so no legend. */
export function TimeSeriesChart({
  data,
  name,
  format = (v) => String(v),
  kind = "area",
  height = 200,
  withTime = false,
  hourlyTicks = false,
  emptyMessage = "No measurements yet. The first sample appears within a few minutes.",
  className,
}: {
  data: Point[];
  name: string;
  format?: (v: number) => string;
  kind?: "area" | "bar";
  height?: number;
  withTime?: boolean;
  /** Label the x axis with hours instead of days (for series spanning about a day). */
  hourlyTicks?: boolean;
  emptyMessage?: string;
  className?: string;
}) {
  const id = React.useId().replace(/:/g, "");
  if (!data.length) return <Empty message={emptyMessage} height={height} />;
  const common = {
    data,
    margin: { top: 8, right: 8, bottom: 0, left: 0 },
  };
  const axes = (
    <>
      <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeWidth={1} />
      <XAxis
        dataKey="t"
        tickFormatter={hourlyTicks ? fmtHour : fmtDay}
        tick={{ fill: "var(--chart-axis)", fontSize: 11 }}
        axisLine={{ stroke: "var(--chart-grid)" }}
        tickLine={false}
        minTickGap={24}
      />
      <YAxis
        tickFormatter={(v: number) => format(v)}
        tick={{ fill: "var(--chart-axis)", fontSize: 11 }}
        axisLine={false}
        tickLine={false}
        width={64}
        allowDecimals={false}
      />
      <Tooltip
        cursor={kind === "area" ? { stroke: "var(--chart-axis)", strokeDasharray: "3 3" } : { fill: "var(--surface-2)" }}
        content={<ChartTooltip format={format} name={name} withTime={withTime} />}
      />
    </>
  );
  return (
    <figure className={cn("w-full", className)} aria-label={`${name} chart`}>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          {kind === "area" ? (
            <AreaChart {...common}>
              <defs>
                <linearGradient id={`fill-${id}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.18} />
                  <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} />
                </linearGradient>
              </defs>
              {axes}
              <Area
                type="monotone"
                dataKey="v"
                name={name}
                stroke="var(--chart-1)"
                strokeWidth={2}
                fill={`url(#fill-${id})`}
                dot={data.length === 1 ? { r: 4, fill: "var(--chart-1)" } : false}
                activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
                isAnimationActive={false}
              />
            </AreaChart>
          ) : (
            <BarChart {...common} barCategoryGap={2}>
              {axes}
              <Bar dataKey="v" name={name} fill="var(--chart-1)" radius={[4, 4, 0, 0]} maxBarSize={28} isAnimationActive={false} />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
      <table className="sr-only">
        <caption>{name}</caption>
        <thead>
          <tr>
            <th>Time</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          {data.slice(-30).map((p) => (
            <tr key={p.t}>
              <td>{fmtFull(p.t, withTime)}</td>
              <td>{format(p.v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
