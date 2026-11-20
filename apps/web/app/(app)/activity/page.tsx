"use client";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ScrollText } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { NativeSelect } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tooltip } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { ACTOR_LABELS, actionLabel } from "@/lib/labels";
import type { ApiKey, AuditEvent, Page } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

export default function ActivityPage() {
  const [page, setPage] = React.useState(1);
  const [action, setAction] = React.useState("");
  const [actor, setActor] = React.useState("");
  const [keyId, setKeyId] = React.useState("");
  React.useEffect(() => setPage(1), [action, actor, keyId]);
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: () => api<ApiKey[]>("/api-keys") });
  const keyName = React.useMemo(() => new Map((keys.data ?? []).map((k) => [k.id, k.name])), [keys.data]);
  const q = useQuery({
    queryKey: ["activity", page, action, actor, keyId],
    queryFn: () => api<Page<AuditEvent>>("/activity", { query: { page, page_size: 30, action, actor_type: actor, api_key_id: keyId } }),
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <PageHeader title="Activity" description="An audit log of sensitive actions on your account, including who performed them." />
      <div className="mb-4 flex flex-col gap-2 sm:flex-row">
        <NativeSelect aria-label="Filter by category" className="sm:w-52" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">All activity</option><option value="auth">Sign-in & security</option><option value="database">Databases</option>
          <option value="project">Projects</option><option value="api_key">API keys</option><option value="billing">Billing</option><option value="admin">Support actions</option>
        </NativeSelect>
        <NativeSelect aria-label="Filter by actor" className="sm:w-44" value={actor} onChange={(e) => setActor(e.target.value)}>
          <option value="">Any actor</option><option value="user">You (dashboard)</option><option value="api_key">API keys</option><option value="system">System</option><option value="admin">Support</option>
        </NativeSelect>
        {keys.data && keys.data.length > 0 && (
          <NativeSelect aria-label="Filter by API key" className="sm:w-52" value={keyId} onChange={(e) => setKeyId(e.target.value)}>
            <option value="">Any API key</option>
            {keys.data.map((k) => <option key={k.id} value={k.id}>{k.kind === "agent" ? "🤖 " : ""}{k.name}</option>)}
          </NativeSelect>
        )}
      </div>
      <Card>
        {!q.data ? <div className="p-5"><Skeleton className="h-48" /></div> : q.data.items.length === 0 ? (
          <EmptyState icon={ScrollText} title="No activity found" />
        ) : (
          <Table>
            <THead><TR><TH>Action</TH><TH>Actor</TH><TH>Resource</TH><TH>IP address</TH><TH>When</TH></TR></THead>
            <TBody>
              {q.data.items.map((e) => (
                <TR key={e.id}>
                  <TD>
                    <span className={e.outcome === "failure" ? "text-critical" : "font-medium"}>{actionLabel(e.action)}</span>
                    {e.reason && <div className="text-xs text-muted">Reason: {e.reason}</div>}
                  </TD>
                  <TD>
                    <Badge variant={e.actor_type === "admin" ? "warning" : "outline"}>{ACTOR_LABELS[e.actor_type] ?? e.actor_type}</Badge>
                    {e.actor_api_key_id && keyName.get(e.actor_api_key_id) && <div className="mt-0.5 text-xs text-muted">{keyName.get(e.actor_api_key_id)}</div>}
                  </TD>
                  <TD className="text-muted">{e.resource_type ? <Tooltip content={e.resource_id ?? ""}><span className="cursor-default">{e.resource_type}</span></Tooltip> : "—"}</TD>
                  <TD className="font-mono text-xs text-muted">{e.ip ?? "—"}</TD>
                  <TD className="whitespace-nowrap text-muted"><Tooltip content={`Request ${e.request_id ?? "n/a"}`}><span>{formatDateTime(e.created_at)}</span></Tooltip></TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Card>
      {q.data && <Pagination page={page} pageSize={30} total={q.data.total} onPage={setPage} />}
    </>
  );
}
