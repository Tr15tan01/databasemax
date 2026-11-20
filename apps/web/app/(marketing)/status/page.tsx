import { CheckCircle2, CircleHelp, XCircle } from "lucide-react";
import type { Metadata } from "next";

import { PageHero } from "@/features/marketing/page-hero";
import { serverGet } from "@/lib/server/api";

export const metadata: Metadata = { title: "Status" };
export const dynamic = "force-dynamic";

interface Status {
  status: string;
  region: string;
  checked_at: string;
  components: { name: string; status: string; detail: string }[];
}

export default async function StatusPage() {
  const s = await serverGet<Status>("/status", 0);
  const ok = s?.status === "operational";
  return (
    <>
      <PageHero eyebrow="Status" title={s ? (ok ? "All systems operational" : "Some systems are degraded") : "Status unavailable"} />
      <div className="mx-auto max-w-3xl px-5 py-14">
        {!s ? (
          <p className="rounded-lg border border-critical/30 bg-critical-soft p-5 text-sm text-critical">
            The status service could not be reached. This usually means the API itself is unavailable.
          </p>
        ) : (
          <>
            <div className="divide-y divide-border rounded-xl border border-border bg-surface">
              {s.components.map((c) => (
                <div key={c.name} className="flex items-center justify-between gap-4 px-5 py-4">
                  <div>
                    <div className="text-sm font-medium">{c.name}</div>
                    <div className="text-[13px] text-muted">{c.detail}</div>
                  </div>
                  <span className={`flex items-center gap-1.5 text-[13px] font-medium ${c.status === "operational" ? "text-good" : c.status === "unknown" ? "text-muted" : "text-critical"}`}>
                    {c.status === "operational" ? <CheckCircle2 className="size-4" /> : c.status === "unknown" ? <CircleHelp className="size-4" /> : <XCircle className="size-4" />}
                    {c.status === "operational" ? "Operational" : c.status === "unknown" ? "Not monitored" : "Degraded"}
                  </span>
                </div>
              ))}
            </div>
            <p className="mt-4 text-xs text-subtle">
              Region: {s.region} · Live check at {new Date(s.checked_at).toUTCString()}. This page reflects real-time checks only;
              incident history isn&apos;t published yet.
            </p>
          </>
        )}
      </div>
    </>
  );
}
