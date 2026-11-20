"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CreditCard, ExternalLink, Info, Loader2, Receipt } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { QuotaList } from "@/features/app/quota-list";
import { api, errorMessage } from "@/lib/api";
import type { Payment, Plan, Quotas, Subscription } from "@/lib/types";
import { cn, formatCents, formatDate } from "@/lib/utils";

declare global {
  interface Window {
    Paddle?: {
      Environment: { set: (env: string) => void };
      Initialize: (opts: { token: string; eventCallback?: (e: { name: string }) => void }) => void;
      Checkout: { open: (opts: { transactionId: string; customer?: { email: string } }) => void };
    };
  }
}

function loadPaddle(): Promise<void> {
  if (window.Paddle) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://cdn.paddle.com/paddle/v2/paddle.js";
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Couldn't load the payment form."));
    document.head.appendChild(s);
  });
}

const STATUS_TEXT: Record<string, string> = {
  active: "Active", trialing: "Trial", past_due: "Payment failed — grace period", paused: "Paused", canceled: "Canceled", grace: "Grace period", suspended: "Suspended",
};

export default function BillingPage() {
  const qc = useQueryClient();
  const sub = useQuery({ queryKey: ["subscription"], queryFn: () => api<Subscription>("/billing/subscription") });
  const plans = useQuery({ queryKey: ["billing-plans"], queryFn: () => api<Plan[]>("/billing/plans") });
  const quotas = useQuery({ queryKey: ["usage-quotas"], queryFn: () => api<Quotas>("/usage/quotas") });
  const payments = useQuery({ queryKey: ["payments"], queryFn: () => api<Payment[]>("/billing/payments") });
  const [awaiting, setAwaiting] = React.useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = React.useState(false);

  // After checkout we wait for the verified webhook to change the plan — the browser's
  // "success" is never trusted.
  React.useEffect(() => {
    if (!awaiting) return;
    const t = setInterval(() => qc.invalidateQueries({ queryKey: ["subscription"] }), 3000);
    return () => clearInterval(t);
  }, [awaiting, qc]);
  React.useEffect(() => {
    if (awaiting && sub.data?.plan === awaiting && sub.data.status === "active") {
      setAwaiting(null);
      toast.success(`You're on the ${sub.data.plan_name} plan`);
      qc.invalidateQueries({ queryKey: ["me"] });
      qc.invalidateQueries({ queryKey: ["usage-quotas"] });
    }
  }, [awaiting, sub.data, qc]);

  const checkout = useMutation({
    mutationFn: async (plan: string) => {
      const s = sub.data!;
      if (s.has_billing_account && s.plan !== "free" && s.status !== "canceled") {
        await api("/billing/change-plan", { method: "POST", body: { plan } });
        return { plan, changed: true };
      }
      const out = await api<{ client_token: string; environment: string; transaction_id: string; customer_email: string }>("/billing/checkout", { method: "POST", body: { plan } });
      await loadPaddle();
      window.Paddle!.Environment.set(out.environment);
      window.Paddle!.Initialize({ token: out.client_token });
      window.Paddle!.Checkout.open({ transactionId: out.transaction_id, customer: { email: out.customer_email } });
      return { plan, changed: false };
    },
    onSuccess: ({ plan, changed }) => {
      setAwaiting(plan);
      if (changed) toast.message("Plan change requested", { description: "We'll update your limits as soon as Paddle confirms." });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const portal = useMutation({
    mutationFn: () => api<{ url: string }>("/billing/portal", { method: "POST" }),
    onSuccess: ({ url }) => { if (url) window.open(url, "_blank", "noopener,noreferrer"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const cancel = useMutation({
    mutationFn: () => api("/billing/cancel", { method: "POST" }),
    onSuccess: () => { setConfirmCancel(false); toast.success("Cancellation requested", { description: "Your plan stays active until the end of the period." }); qc.invalidateQueries({ queryKey: ["subscription"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (sub.isError) return <ErrorState error={sub.error} onRetry={() => sub.refetch()} />;
  const s = sub.data;
  const billingEnabled = s?.billing_enabled;
  return (
    <>
      <PageHeader title="Billing" description="Your plan, limits and payment history. Payments are processed securely by Paddle."
        actions={s?.has_billing_account && <Button variant="secondary" onClick={() => portal.mutate()} loading={portal.isPending}><ExternalLink /> Billing portal</Button>} />
      {s && !billingEnabled && (
        <div className="mb-6 flex gap-3 rounded-lg border border-border bg-surface-2 px-4 py-3 text-[13px] text-muted">
          <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
          Paid plans aren&apos;t available on this deployment yet because payments haven&apos;t been configured. You can keep using the Free plan.
        </div>
      )}
      {awaiting && (
        <div role="status" className="mb-6 flex items-center gap-3 rounded-lg border border-accent/30 bg-accent-soft px-4 py-3 text-[13px]">
          <Loader2 className="size-4 animate-spin text-accent" /> Waiting for payment confirmation from Paddle. This page updates automatically.
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setAwaiting(null)}>Dismiss</Button>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
        <Card>
          <CardHeader>
            <div>
              <CardDescription>Current plan</CardDescription>
              <div className="mt-1 flex items-center gap-3">
                <span className="text-2xl font-semibold tracking-[-0.02em]">{s?.plan_name ?? "…"}</span>
                {s && <Badge variant={s.status === "active" ? "good" : s.status === "past_due" ? "warning" : "neutral"}>{STATUS_TEXT[s.status] ?? s.status}</Badge>}
              </div>
            </div>
            <CreditCard className="size-5 text-subtle" />
          </CardHeader>
          <CardContent>
            {!s ? <Skeleton className="h-20" /> : (
              <dl className="grid grid-cols-2 gap-4 text-[13px]">
                <div><dt className="text-subtle">Price</dt><dd className="mt-0.5 font-medium">{formatCents(s.price_cents)} / month</dd></div>
                <div><dt className="text-subtle">{s.cancel_at_period_end ? "Ends on" : "Renews on"}</dt><dd className="mt-0.5 font-medium">{s.plan === "free" ? "—" : formatDate(s.period_end)}</dd></div>
                {s.effective_plan !== s.plan && (
                  <div className="col-span-2 rounded-md bg-warning-soft px-3 py-2 text-foreground">Limits currently follow the <strong>{s.effective_plan}</strong> plan because the subscription isn&apos;t in good standing.</div>
                )}
                {s.grace_until && (
                  <div className="col-span-2 rounded-md bg-warning-soft px-3 py-2">Grace period until {formatDate(s.grace_until)}. Update your payment method in the billing portal to avoid limits changing. Your databases will never be deleted because of a failed payment.</div>
                )}
                {s.cancel_at_period_end && <div className="col-span-2 text-muted">Your subscription is canceled and ends at the end of this period. You&apos;ll move to the Free plan; nothing is deleted.</div>}
              </dl>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><div><CardTitle>Usage vs. plan</CardTitle><CardDescription>Limits are enforced server-side.</CardDescription></div></CardHeader>
          <CardContent>{quotas.data ? <QuotaList items={quotas.data.items} /> : <Skeleton className="h-36" />}</CardContent>
        </Card>
      </div>

      <h2 className="mb-3 mt-10 text-sm font-semibold">Plans</h2>
      <div className="grid gap-4 md:grid-cols-3">
        {plans.data?.map((p) => {
          const current = s?.plan === p.id && s.status !== "canceled";
          const rank = { free: 0, developer: 1, pro: 2 } as Record<string, number>;
          const up = (rank[p.id] ?? 0) > (rank[s?.plan ?? "free"] ?? 0);
          return (
            <Card key={p.id} className={cn("flex flex-col p-5", current && "border-foreground/70")}>
              <div className="flex items-center justify-between">
                <h3 className="font-semibold">{p.name}</h3>
                {current && <Badge variant="accent">Current</Badge>}
              </div>
              <div className="mt-3 text-3xl font-semibold tracking-[-0.03em]">{formatCents(p.price_cents)}<span className="text-sm font-normal text-subtle">/mo</span></div>
              <ul className="mt-4 flex-1 space-y-2 text-[13px] text-muted">
                {p.features.slice(0, 6).map((f) => <li key={f} className="flex gap-2"><Check className="mt-0.5 size-3.5 shrink-0 text-foreground" />{f}</li>)}
              </ul>
              <div className="mt-5">
                {current ? (
                  <Button variant="secondary" className="w-full" disabled>Current plan</Button>
                ) : p.id === "free" ? (
                  <Button variant="secondary" className="w-full" disabled={!s?.has_billing_account || s?.cancel_at_period_end} onClick={() => setConfirmCancel(true)}>
                    {s?.cancel_at_period_end ? "Downgrade scheduled" : "Downgrade to Free"}
                  </Button>
                ) : (
                  <Button className="w-full" variant={up ? "primary" : "secondary"} disabled={!billingEnabled || !p.purchasable || !!awaiting}
                    loading={checkout.isPending && checkout.variables === p.id} onClick={() => checkout.mutate(p.id)}>
                    {up ? `Upgrade to ${p.name}` : `Switch to ${p.name}`}
                  </Button>
                )}
              </div>
            </Card>
          );
        })}
      </div>

      <Card className="mt-10">
        <CardHeader><div><CardTitle>Payment history</CardTitle><CardDescription>Invoices are available in the billing portal.</CardDescription></div></CardHeader>
        {payments.data && payments.data.length === 0 ? (
          <EmptyState icon={Receipt} title="No payments yet" description="Payments appear here after Paddle confirms them." />
        ) : (
          <Table>
            <THead><TR><TH>Date</TH><TH>Description</TH><TH>Invoice</TH><TH>Status</TH><TH className="text-right">Amount</TH></TR></THead>
            <TBody>
              {payments.data?.map((p) => (
                <TR key={p.id}>
                  <TD>{formatDate(p.billed_at)}</TD>
                  <TD className="text-muted">{p.description}</TD>
                  <TD className="font-mono text-xs text-muted">{p.invoice_number ?? "—"}</TD>
                  <TD><Badge variant={p.status === "completed" ? "good" : p.status === "failed" ? "critical" : "neutral"}>{p.status}</Badge></TD>
                  <TD className="text-right tabular">{formatCents(p.amount_cents, p.currency)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Card>
      <ConfirmDialog open={confirmCancel} onOpenChange={setConfirmCancel} title="Cancel your subscription?"
        description="Your plan stays active until the end of the billing period, then your account moves to the Free plan. Databases above the Free limits will be stopped after a 14-day grace period — never deleted."
        confirmLabel="Cancel subscription" loading={cancel.isPending} onConfirm={() => cancel.mutate()} />
    </>
  );
}
