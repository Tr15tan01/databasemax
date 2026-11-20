import { Activity, Database, FolderKanban, HardDrive, Plug } from "lucide-react";

import { LogoMark } from "@/components/logo";

/** Static illustration of the dashboard used on marketing pages (not live data). */
export function DashboardPreview() {
  const bars = [22, 28, 26, 34, 31, 38, 42, 40, 47, 45, 52, 58, 55, 61];
  return (
    <figure className="relative">
      <div
        aria-hidden
        className="overflow-hidden rounded-xl border border-border bg-surface shadow-[0_30px_80px_-20px_rgb(var(--shadow-color)/0.35)]"
      >
        <div className="flex items-center gap-1.5 border-b border-border bg-surface-2/60 px-4 py-2.5">
          <span className="size-2.5 rounded-full bg-border-strong" />
          <span className="size-2.5 rounded-full bg-border-strong" />
          <span className="size-2.5 rounded-full bg-border-strong" />
          <span className="ml-3 rounded-md border border-border bg-surface px-2.5 py-0.5 font-mono text-[10px] text-subtle">
            app.databasemax.dev/dashboard
          </span>
        </div>
        <div className="grid grid-cols-[150px_1fr] max-sm:grid-cols-1">
          <aside className="hidden space-y-1 border-r border-border p-3 text-[11px] sm:block">
            <div className="mb-3 flex items-center gap-1.5 font-semibold">
              <LogoMark className="size-5" /> DatabaseMax
            </div>
            {["Dashboard", "Projects", "Databases", "Usage", "Billing", "API keys"].map((l, i) => (
              <div key={l} className={i === 0 ? "rounded-md bg-surface-2 px-2 py-1.5 font-medium" : "px-2 py-1.5 text-muted"}>
                {l}
              </div>
            ))}
          </aside>
          <div className="space-y-4 p-4 sm:p-5">
            <div className="text-sm font-semibold">Overview</div>
            <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
              {[
                { icon: FolderKanban, label: "Projects", value: "3" },
                { icon: Database, label: "Databases", value: "4" },
                { icon: HardDrive, label: "Storage used", value: "1.8 GB" },
                { icon: Plug, label: "Connections", value: "27" },
              ].map((s) => (
                <div key={s.label} className="rounded-lg border border-border p-3">
                  <div className="flex items-center justify-between text-[10px] text-muted">
                    {s.label} <s.icon className="size-3" />
                  </div>
                  <div className="mt-1 text-lg font-semibold tracking-tight">{s.value}</div>
                </div>
              ))}
            </div>
            <div className="grid gap-2.5 lg:grid-cols-[1.6fr_1fr]">
              <div className="rounded-lg border border-border p-3">
                <div className="mb-2 flex items-center justify-between text-[10px] text-muted">
                  <span>Storage · last 14 days</span>
                  <Activity className="size-3" />
                </div>
                <div className="flex h-24 items-end gap-[3px]">
                  {bars.map((h, i) => (
                    <div key={i} className="flex-1 rounded-t-[3px] bg-accent/80" style={{ height: `${h}%` }} />
                  ))}
                </div>
              </div>
              <div className="space-y-2 rounded-lg border border-border p-3 text-[10px]">
                <div className="text-muted">Databases</div>
                {[
                  ["orders-prod", "Running", "text-good"],
                  ["analytics", "Running", "text-good"],
                  ["staging", "Stopped", "text-muted"],
                  ["feature-x", "Provisioning", "text-accent"],
                ].map(([n, s, c]) => (
                  <div key={n} className="flex items-center justify-between border-t border-border pt-2 first-of-type:border-0">
                    <span className="font-mono">{n}</span>
                    <span className={c}>● {s}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="rounded-lg border border-border bg-surface-2/60 p-3 font-mono text-[10px] text-muted">
              postgresql://u_k3j9x2ab1c:••••••••@db-k3j9x2ab1c.db.databasemax.dev:5432/orders?sslmode=require
            </div>
          </div>
        </div>
      </div>
      <figcaption className="sr-only">Illustration of the DatabaseMax dashboard.</figcaption>
    </figure>
  );
}
