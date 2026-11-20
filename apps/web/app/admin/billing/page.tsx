"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatCents, formatDate, formatDateTime } from "@/lib/utils";

interface B {
  billing_enabled: boolean;
  subscriptions: { user_id: string; email: string; plan: string; status: string; period_end: string | null; cancel_at_period_end: boolean; grace_until: string | null }[];
  payments: { id: string; email: string; status: string; amount_cents: number; currency: string; billed_at: string | null }[];
  events: { id: string; event_type: string; status: string; received_at: string; user_id: string | null; error: string | null }[];
}

export default function AdminBilling() {
  const q = useQuery({ queryKey: ["admin", "billing"], queryFn: () => api<B>("/admin/billing") });
  const d = q.data;
  return (
    <>
      <PageHeader title="Billing" description={d ? (d.billing_enabled ? "Paddle is configured. State changes come only from verified webhooks and reconciliation." : "Paddle is not configured on this deployment.") : ""} />
      {!d ? <Skeleton className="h-80" /> : (
        <div className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Paid subscriptions</CardTitle></CardHeader>
            <Table>
              <THead><TR><TH>Customer</TH><TH>Plan</TH><TH>Status</TH><TH>Period end</TH><TH>Grace until</TH></TR></THead>
              <TBody>
                {d.subscriptions.map((s) => (
                  <TR key={s.user_id}>
                    <TD><Link href={`/admin/users/${s.user_id}`} className="hover:underline">{s.email}</Link></TD>
                    <TD className="capitalize">{s.plan}</TD>
                    <TD><Badge variant={s.status === "active" ? "good" : s.status === "past_due" ? "warning" : "neutral"}>{s.status}{s.cancel_at_period_end && " · canceling"}</Badge></TD>
                    <TD className="text-muted">{formatDate(s.period_end)}</TD>
                    <TD className="text-muted">{formatDate(s.grace_until)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            {d.subscriptions.length === 0 && <p className="p-5 text-[13px] text-subtle">No paid subscriptions yet.</p>}
          </Card>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader><CardTitle>Payments</CardTitle></CardHeader>
              <Table>
                <THead><TR><TH>Customer</TH><TH>Status</TH><TH className="text-right">Amount</TH><TH>Billed</TH></TR></THead>
                <TBody>
                  {d.payments.map((p) => (
                    <TR key={p.id}><TD>{p.email}</TD><TD><Badge variant={p.status === "completed" ? "good" : p.status === "failed" ? "critical" : "neutral"}>{p.status}</Badge></TD><TD className="text-right tabular">{formatCents(p.amount_cents, p.currency)}</TD><TD className="text-muted">{formatDate(p.billed_at)}</TD></TR>
                  ))}
                </TBody>
              </Table>
              {d.payments.length === 0 && <p className="p-5 text-[13px] text-subtle">No payments recorded.</p>}
            </Card>
            <Card>
              <CardHeader><CardTitle>Webhook events</CardTitle></CardHeader>
              <Table>
                <THead><TR><TH>Event</TH><TH>Result</TH><TH>Received</TH></TR></THead>
                <TBody>
                  {d.events.map((e) => (
                    <TR key={e.id}><TD className="font-mono text-xs">{e.event_type}</TD><TD><Badge variant={e.status === "processed" ? "good" : "warning"}>{e.status}</Badge></TD><TD className="text-muted">{formatDateTime(e.received_at)}</TD></TR>
                  ))}
                </TBody>
              </Table>
              {d.events.length === 0 && <p className="p-5 text-[13px] text-subtle">No webhook events received.</p>}
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
