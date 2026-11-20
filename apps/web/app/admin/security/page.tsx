"use client";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Eye, KeyRound, ShieldAlert, ShieldCheck, UserCog } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Stat } from "@/components/ui/stat";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { ADMIN_ACTOR_LABELS, actionLabel } from "@/lib/labels";
import type { AuditEvent } from "@/lib/types";
import { formatDateTime, formatNumber } from "@/lib/utils";

interface Security {
  failed_logins: { last_hour: number; last_24h: number };
  top_failed_ips_1h: { ip: string; count: number }[];
  accounts_with_failures_1h: number;
  max_failures_single_account_1h: number;
  credential_reveals_24h: number;
  security_actions_24h: Record<string, number>;
  admin_actions_24h: number;
  failed_operations_24h: number;
  thresholds: Record<string, number>;
  alerts: { severity: "critical" | "warning" | "info"; message: string }[];
  recent_events: AuditEvent[];
  generated_at: string;
}

const SEV = { critical: "critical", warning: "warning", info: "neutral" } as const;

export default function AdminSecurity() {
  const q = useQuery({ queryKey: ["admin", "security"], queryFn: () => api<Security>("/admin/security"), refetchInterval: 30_000 });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const s = q.data;
  return (
    <>
      <PageHeader title="Security" description="Signals from sign-in attempts and the audit log. Thresholds are documented in the incident-response runbook. Refreshes every 30 seconds." />
      {!s ? <div className="grid gap-4 md:grid-cols-4">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-28" />)}</div> : (
        <>
          <Card className="mb-4">
            <CardContent className="py-4">
              {s.alerts.length === 0 ? (
                <p className="flex items-center gap-2 text-[13px] text-good"><ShieldCheck className="size-4" /> No security alerts right now.</p>
              ) : (
                <ul className="space-y-2">
                  {s.alerts.map((a, i) => (
                    <li key={i} className="flex items-center gap-2 text-[13px]"><Badge variant={SEV[a.severity]}><AlertTriangle /> {a.severity}</Badge>{a.message}</li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Failed sign-ins (1 h)" icon={ShieldAlert} value={formatNumber(s.failed_logins.last_hour)}
              sub={`${formatNumber(s.failed_logins.last_24h)} in 24 h · alert at ${s.thresholds.failed_logins_per_hour}/h`} />
            <Stat label="Accounts targeted (1 h)" icon={UserCog} value={s.accounts_with_failures_1h}
              sub={`max ${s.max_failures_single_account_1h} failures on one account`} />
            <Stat label="Password reveals (24 h)" icon={Eye} value={s.credential_reveals_24h} sub="each one is audited" />
            <Stat label="Admin actions (24 h)" icon={KeyRound} value={s.admin_actions_24h} sub={`${s.failed_operations_24h} failed operations`} />
          </div>
          <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_1.4fr]">
            <Card>
              <CardHeader><div><CardTitle>Sensitive actions (24 h)</CardTitle><CardDescription>Counts by audit action</CardDescription></div></CardHeader>
              <CardContent>
                {Object.keys(s.security_actions_24h).length === 0 ? <p className="text-[13px] text-subtle">None.</p> : (
                  <ul className="space-y-2 text-[13px]">
                    {Object.entries(s.security_actions_24h).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
                      <li key={k} className="flex justify-between gap-4"><span>{actionLabel(k)}</span><span className="tabular text-muted">{v}</span></li>
                    ))}
                  </ul>
                )}
                {s.top_failed_ips_1h.length > 0 && (
                  <>
                    <h4 className="mb-2 mt-5 text-xs font-medium text-muted">Top sources of failed sign-ins (1 h)</h4>
                    <ul className="space-y-1 font-mono text-xs">{s.top_failed_ips_1h.map((x) => <li key={x.ip} className="flex justify-between"><span>{x.ip}</span><span>{x.count}</span></li>)}</ul>
                  </>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Recent security events</CardTitle></CardHeader>
              <Table>
                <THead><TR><TH>Event</TH><TH>Actor</TH><TH>Outcome</TH><TH>When</TH></TR></THead>
                <TBody>
                  {s.recent_events.slice(0, 25).map((e) => (
                    <TR key={e.id}>
                      <TD className="font-medium">{actionLabel(e.action)}{e.reason && <div className="text-xs text-subtle">{e.reason}</div>}</TD>
                      <TD className="text-muted">{ADMIN_ACTOR_LABELS[e.actor_type] ?? e.actor_type}</TD>
                      <TD><Badge variant={e.outcome === "failure" ? "critical" : "neutral"}>{e.outcome}</Badge></TD>
                      <TD className="whitespace-nowrap text-muted">{formatDateTime(e.created_at)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </Card>
          </div>
        </>
      )}
    </>
  );
}
