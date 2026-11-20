"use client";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Card } from "@/components/ui/card";
import { Input, NativeSelect } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatBytes, formatDate, formatMB } from "@/lib/utils";

interface Row { id: string; name: string; owner_id: string; owner_email: string; project: string; status: string; status_reason: string | null; postgres_version: string; compute_profile: string; allocated_storage_mb: number; storage_used_bytes: number | null; connections: number | null; max_connections: number; host: string | null; route_id: string; instance: string; created_at: string; suspended_by_admin: boolean }

export default function AdminDatabases() {
  const [q, setQ] = React.useState("");
  const [dq, setDq] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [page, setPage] = React.useState(1);
  React.useEffect(() => { const t = setTimeout(() => setDq(q), 250); return () => clearTimeout(t); }, [q]);
  React.useEffect(() => setPage(1), [dq, status]);
  const list = useQuery({
    queryKey: ["admin", "databases", dq, status, page],
    queryFn: () => api<{ items: Row[]; total: number }>("/admin/databases", { query: { q: dq, status, page, page_size: 25 } }),
    placeholderData: keepPreviousData, refetchInterval: 15_000,
  });
  return (
    <>
      <PageHeader title="Databases" description="Every customer database, its placement and state. Passwords are never shown here." />
      <div className="mb-4 grid gap-2 sm:grid-cols-[1fr_180px]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-subtle" />
          <Input aria-label="Search databases" className="pl-8" placeholder="Name, owner email, route id, instance or ID…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <NativeSelect aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All live</option>
          {["ready", "queued", "provisioning", "configuring", "testing", "failed", "stopped", "deleting", "deleted"].map((s) => <option key={s} value={s}>{s}</option>)}
        </NativeSelect>
      </div>
      <Card>
        {!list.data ? <div className="p-5"><Skeleton className="h-60" /></div> : (
          <Table>
            <THead><TR><TH>Database</TH><TH>Owner</TH><TH>Status</TH><TH>Version / size</TH><TH>Storage</TH><TH>Conn.</TH><TH>Host / route</TH><TH>Created</TH></TR></THead>
            <TBody>
              {list.data.items.map((d) => (
                <TR key={d.id} className="hover:bg-surface-2/60">
                  <TD><Link href={`/admin/databases/${d.id}`} className="font-medium hover:underline">{d.name}</Link><div className="text-xs text-subtle">{d.project}</div></TD>
                  <TD><Link href={`/admin/users/${d.owner_id}`} className="text-muted hover:underline">{d.owner_email}</Link></TD>
                  <TD><StatusBadge status={d.suspended_by_admin ? "suspended" : d.status} />{d.status === "failed" && d.status_reason && <div className="mt-1 max-w-48 truncate text-xs text-critical">{d.status_reason}</div>}</TD>
                  <TD className="text-muted">PG {d.postgres_version} · {d.compute_profile}</TD>
                  <TD className="tabular text-muted">{formatBytes(d.storage_used_bytes)} / {formatMB(d.allocated_storage_mb)}</TD>
                  <TD className="tabular text-muted">{d.connections ?? "—"}/{d.max_connections}</TD>
                  <TD className="font-mono text-xs text-muted">{d.host ?? "—"}<div>{d.route_id}</div></TD>
                  <TD className="text-muted">{formatDate(d.created_at)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Card>
      {list.data && <Pagination page={page} pageSize={25} total={list.data.total} onPage={setPage} />}
    </>
  );
}
