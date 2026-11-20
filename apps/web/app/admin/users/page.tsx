"use client";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Input, NativeSelect } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatBytes, formatDate, relativeTime } from "@/lib/utils";

interface Row { id: string; email: string; display_name: string | null; status: string; verified: boolean; created_at: string; last_login_at: string | null; plan: string; subscription_status: string | null; databases: number; storage_used_bytes: number; is_admin: boolean }

export default function AdminUsers() {
  const [q, setQ] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [plan, setPlan] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [dq, setDq] = React.useState("");
  React.useEffect(() => { const t = setTimeout(() => setDq(q), 250); return () => clearTimeout(t); }, [q]);
  React.useEffect(() => setPage(1), [dq, status, plan]);
  const list = useQuery({
    queryKey: ["admin", "users", dq, status, plan, page],
    queryFn: () => api<{ items: Row[]; total: number }>("/admin/users", { query: { q: dq, status, plan, page, page_size: 25 } }),
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <PageHeader title="Users" description={list.data ? `${list.data.total} matching account(s)` : "Search accounts by email, name or ID."} />
      <div className="mb-4 grid gap-2 sm:grid-cols-[1fr_160px_160px]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-subtle" />
          <Input aria-label="Search users" className="pl-8" placeholder="Email, name or user ID…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <NativeSelect aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any status</option><option value="active">Active</option><option value="suspended">Suspended</option></NativeSelect>
        <NativeSelect aria-label="Plan" value={plan} onChange={(e) => setPlan(e.target.value)}><option value="">Any plan</option><option value="free">Free</option><option value="developer">Developer</option><option value="pro">Pro</option></NativeSelect>
      </div>
      <Card>
        {!list.data ? <div className="p-5"><Skeleton className="h-60" /></div> : (
          <Table>
            <THead><TR><TH>User</TH><TH>Status</TH><TH>Plan</TH><TH>Databases</TH><TH>Storage</TH><TH>Last sign-in</TH><TH>Joined</TH></TR></THead>
            <TBody>
              {list.data.items.map((u) => (
                <TR key={u.id} className="hover:bg-surface-2/60">
                  <TD>
                    <Link href={`/admin/users/${u.id}`} className="font-medium hover:underline">{u.email}</Link>
                    <div className="flex gap-1.5 text-xs text-subtle">{u.display_name}{!u.verified && <Badge variant="warning" className="py-0">unverified</Badge>}{u.is_admin && <Badge variant="critical" className="py-0">admin</Badge>}</div>
                  </TD>
                  <TD><StatusBadge status={u.status} /></TD>
                  <TD className="capitalize">{u.plan}{u.subscription_status && u.subscription_status !== "active" && <span className="text-xs text-warning"> · {u.subscription_status}</span>}</TD>
                  <TD className="tabular">{u.databases}</TD>
                  <TD className="tabular text-muted">{formatBytes(u.storage_used_bytes)}</TD>
                  <TD className="text-muted">{relativeTime(u.last_login_at)}</TD>
                  <TD className="text-muted">{formatDate(u.created_at)}</TD>
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
