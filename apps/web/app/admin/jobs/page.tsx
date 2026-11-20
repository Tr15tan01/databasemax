"use client";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatDateTime, relativeTime } from "@/lib/utils";

interface Job { id: string; kind: string; status: string; step: string | null; database_id: string; database_name: string; owner_email: string; attempt_count: number; max_attempts: number; error_code: string | null; safe_error_message: string | null; created_at: string; run_after: string; lease_owner: string | null; duration_seconds: number | null }

export default function AdminJobs() {
  const [status, setStatus] = React.useState("active");
  const [page, setPage] = React.useState(1);
  React.useEffect(() => setPage(1), [status]);
  const q = useQuery({
    queryKey: ["admin", "jobs", status, page],
    queryFn: () => api<{ items: Job[]; total: number }>("/admin/jobs", { query: { status: status || undefined, page, page_size: 50 } }),
    placeholderData: keepPreviousData, refetchInterval: 5000,
  });
  return (
    <>
      <PageHeader title="Provisioning queue" description="Jobs are stored in PostgreSQL, claimed with leases and retried with backoff. Refreshes every 5 seconds."
        actions={
          <NativeSelect aria-label="Status" className="w-44" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="active">Queued & running</option><option value="">All</option><option value="failed">Failed</option><option value="succeeded">Succeeded</option><option value="cancelled">Cancelled</option>
          </NativeSelect>
        } />
      <Card>
        {!q.data ? <div className="p-5"><Skeleton className="h-40" /></div> : q.data.items.length === 0 ? (
          <p className="p-10 text-center text-[13px] text-subtle">{status === "active" ? "The queue is empty." : "No jobs."}</p>
        ) : (
          <Table>
            <THead><TR><TH>Job</TH><TH>Database</TH><TH>Status</TH><TH>Step</TH><TH>Attempts</TH><TH>Error</TH><TH>Worker</TH><TH>Created</TH><TH>Duration</TH></TR></THead>
            <TBody>
              {q.data.items.map((j) => (
                <TR key={j.id}>
                  <TD className="font-medium">{j.kind}<div className="font-mono text-[10px] text-subtle">{j.id.slice(0, 8)}</div></TD>
                  <TD><Link href={`/admin/databases/${j.database_id}`} className="hover:underline">{j.database_name}</Link><div className="text-xs text-subtle">{j.owner_email}</div></TD>
                  <TD><Badge variant={j.status === "succeeded" ? "good" : j.status === "failed" ? "critical" : j.status === "running" ? "accent" : "neutral"}>{j.status}</Badge>
                    {j.status === "queued" && j.attempt_count > 0 && <div className="text-xs text-subtle">retry {relativeTime(j.run_after)}</div>}</TD>
                  <TD className="text-muted">{j.step ?? "—"}</TD>
                  <TD className="tabular">{j.attempt_count}/{j.max_attempts}</TD>
                  <TD className="max-w-60 text-xs text-critical">{j.error_code}</TD>
                  <TD className="max-w-40 truncate font-mono text-[10px] text-subtle">{j.lease_owner ?? "—"}</TD>
                  <TD className="whitespace-nowrap text-muted">{formatDateTime(j.created_at)}</TD>
                  <TD className="tabular text-muted">{j.duration_seconds === null ? "—" : `${j.duration_seconds.toFixed(1)}s`}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Card>
      {q.data && <Pagination page={page} pageSize={50} total={q.data.total} onPage={setPage} />}
    </>
  );
}
