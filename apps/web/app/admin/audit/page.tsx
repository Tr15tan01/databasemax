"use client";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Input, NativeSelect } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

type Row = AuditEvent & { tenant_user_id?: string };

export default function AdminAudit() {
  const [actor, setActor] = React.useState("");
  const [action, setAction] = React.useState("");
  const [outcome, setOutcome] = React.useState("");
  const [page, setPage] = React.useState(1);
  React.useEffect(() => setPage(1), [actor, action, outcome]);
  const q = useQuery({
    queryKey: ["admin", "audit", actor, action, outcome, page],
    queryFn: () => api<{ items: Row[]; total: number }>("/admin/audit", { query: { actor_type: actor, action, outcome, page, page_size: 50 } }),
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <PageHeader title="Audit log" description="Platform-wide, append-only record of sensitive actions. Admin actions include the operator and reason." />
      <div className="mb-4 grid gap-2 sm:grid-cols-[1fr_160px_160px]">
        <Input aria-label="Action prefix" placeholder="Action prefix, e.g. admin. or database." value={action} onChange={(e) => setAction(e.target.value)} />
        <NativeSelect aria-label="Actor" value={actor} onChange={(e) => setActor(e.target.value)}><option value="">Any actor</option><option value="user">User</option><option value="api_key">API key</option><option value="admin">Admin</option><option value="system">System</option></NativeSelect>
        <NativeSelect aria-label="Outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)}><option value="">Any outcome</option><option value="success">Success</option><option value="failure">Failure</option></NativeSelect>
      </div>
      <Card>
        {!q.data ? <div className="p-5"><Skeleton className="h-60" /></div> : (
          <Table>
            <THead><TR><TH>Action</TH><TH>Actor</TH><TH>Target</TH><TH>Reason</TH><TH>Outcome</TH><TH>IP</TH><TH>When</TH></TR></THead>
            <TBody>
              {q.data.items.map((e) => (
                <TR key={e.id}>
                  <TD className="font-mono text-xs">{e.action}</TD>
                  <TD><Badge variant={e.actor_type === "admin" ? "critical" : "outline"}>{e.actor_type}</Badge>
                    {e.actor_user_id && <Link href={`/admin/users/${e.actor_user_id}`} className="ml-1 font-mono text-[10px] text-subtle hover:underline">{e.actor_user_id.slice(0, 8)}</Link>}</TD>
                  <TD className="text-xs text-muted">{e.resource_type}{e.resource_id && <span className="font-mono"> {e.resource_id.slice(0, 12)}</span>}</TD>
                  <TD className="max-w-xs text-muted">{e.reason ?? "—"}</TD>
                  <TD><Badge variant={e.outcome === "success" ? "good" : "critical"}>{e.outcome}</Badge></TD>
                  <TD className="font-mono text-xs text-muted">{e.ip ?? "—"}</TD>
                  <TD className="whitespace-nowrap text-muted">{formatDateTime(e.created_at)}</TD>
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
