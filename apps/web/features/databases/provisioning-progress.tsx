"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, RotateCw, X } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, errorMessage } from "@/lib/api";
import type { Database } from "@/lib/types";
import { cn } from "@/lib/utils";

const STEPS = [
  { key: "validating", label: "Validate plan and quota", help: "We check your plan's limits before reserving anything." },
  { key: "queued", label: "Queue provisioning job", help: "Jobs are durable — they survive restarts and are retried automatically." },
  { key: "provisioning", label: "Create isolated PostgreSQL instance", help: "A dedicated instance with its own storage and CPU/memory limits." },
  { key: "configuring", label: "Create database and credentials", help: "A unique role and database; the password is encrypted at rest." },
  { key: "testing", label: "Verify connectivity over TLS", help: "We connect through the public gateway with your credentials, just like your app will." },
  { key: "ready", label: "Ready", help: "Your connection URL is available." },
];
const ORDER = ["pending", "validating", "queued", "provisioning", "configuring", "testing", "ready"];

export function ProvisioningProgress({ database }: { database: Database }) {
  const qc = useQueryClient();
  const idx = ORDER.indexOf(database.status);
  const failed = database.status === "failed";
  const retry = useMutation({
    mutationFn: () => api(`/databases/${database.id}/retry`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["databases", database.id] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const failedAt = failed ? (database.provisioning?.message ?? "") : "";
  const pct = failed ? 100 : Math.round((Math.max(idx, 0) / (ORDER.length - 1)) * 100);
  const p = database.provisioning;
  return (
    <div>
      <div className="mb-5">
        <div className="mb-2 flex justify-between text-[13px]">
          <span className="font-medium" aria-live="polite">{failed ? "Provisioning failed" : database.status === "ready" ? "Your database is ready" : p?.message ?? "Working…"}</span>
          <span className="tabular text-muted">{failed ? "" : `${pct}%`}</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Provisioning progress">
          <div className={cn("h-full rounded-full transition-[width] duration-700", failed ? "bg-critical" : "bg-accent")} style={{ width: `${pct}%` }} />
        </div>
        {p && p.attempt > 1 && !failed && database.status !== "ready" && (
          <p className="mt-2 text-xs text-warning">Attempt {p.attempt} of {p.max_attempts} — {p.error_code?.replace(/_/g, " ")}. Retrying automatically.</p>
        )}
      </div>
      <ol className="space-y-3">
        {STEPS.map((s) => {
          const sIdx = ORDER.indexOf(s.key);
          const done = !failed && idx > sIdx || database.status === "ready";
          const current = !failed && idx === sIdx && database.status !== "ready";
          return (
            <li key={s.key} className="flex gap-3">
              <span className={cn(
                "mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border text-[10px]",
                done ? "border-good bg-good text-white" : current ? "border-accent text-accent" : "border-border text-subtle",
              )}>
                {done ? <Check className="size-3" /> : current ? <Loader2 className="size-3 animate-spin" /> : null}
              </span>
              <div>
                <div className={cn("text-[13px]", done || current ? "font-medium" : "text-muted")}>{s.label}</div>
                {current && <div className="mt-0.5 text-xs text-muted">{s.help}</div>}
              </div>
            </li>
          );
        })}
      </ol>
      {failed && (
        <div role="alert" className="mt-5 rounded-lg border border-critical/25 bg-critical-soft p-4 text-[13px]">
          <div className="flex gap-2 font-medium text-critical"><X className="size-4" /> We couldn&apos;t finish creating this database.</div>
          <p className="mt-1.5 text-muted">{failedAt || database.status_reason}</p>
          <p className="mt-1.5 text-muted">Nothing was left running and you weren&apos;t charged for it. You can retry now; if it keeps failing, contact <Link href="/support" className="underline">support</Link> with the database ID <code className="font-mono text-xs">{database.id}</code>.</p>
          <Button className="mt-3" size="sm" onClick={() => retry.mutate()} loading={retry.isPending}><RotateCw /> Retry provisioning</Button>
        </div>
      )}
    </div>
  );
}
