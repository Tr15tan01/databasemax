"use client";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, Clock, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import * as React from "react";
import { Suspense } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { FormField } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useReauth } from "@/features/app/reauth-dialog";
import { api, errorMessage } from "@/lib/api";
import { APPROVAL_STATUS } from "@/lib/labels";
import type { Approval, Page } from "@/lib/types";
import { cn, formatDateTime, relativeTime } from "@/lib/utils";

function resourceHref(a: Approval): string | null {
  if (a.resource_type === "database") return `/databases/${a.resource_id}`;
  if (a.resource_type === "project") return `/projects/${a.resource_id}`;
  return null;
}

function expiresIn(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "expiring";
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  return h > 0 ? `${h} h ${m} min left` : `${m} min left`;
}

function StatusBadge({ status }: { status: string }) {
  const s = APPROVAL_STATUS[status] ?? { label: status, variant: "neutral" as const };
  return <Badge variant={s.variant}>{s.label}</Badge>;
}

function PendingCard({ a, highlighted, onApprove, onReject }: { a: Approval; highlighted: boolean; onApprove: () => void; onReject: () => void }) {
  const href = resourceHref(a);
  return (
    <Card id={`approval-${a.id}`} data-testid="pending-approval" className={cn("p-5", highlighted && "ring-2 ring-warning/60")}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[15px] font-semibold">{a.action_label}</span>
            <StatusBadge status={a.status} />
          </div>
          <p className="text-[13px] text-foreground first-letter:uppercase">{a.summary}</p>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
            <span className="flex items-center gap-1"><Bot className="size-3.5" aria-hidden /> {a.api_key_name ?? "Deleted key"}</span>
            <span>Requested {relativeTime(a.created_at)}</span>
            <span className="flex items-center gap-1"><Clock className="size-3.5" aria-hidden /> {expiresIn(a.expires_at)}</span>
            {href && <Link href={href} className="text-accent hover:underline">View {a.resource_type}</Link>}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button variant="secondary" size="sm" onClick={onReject}><X /> Reject</Button>
          <Button variant="danger" size="sm" onClick={onApprove}><Check /> Approve</Button>
        </div>
      </div>
    </Card>
  );
}

function ApprovalsInner() {
  const qc = useQueryClient();
  const focus = useSearchParams().get("id");
  const { run, dialog } = useReauth();
  const [status, setStatus] = React.useState("");
  const [page, setPage] = React.useState(1);
  React.useEffect(() => setPage(1), [status]);
  const pending = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => api<Page<Approval>>("/approvals", { query: { status: "pending", page_size: 100 } }),
    refetchInterval: 15_000,
  });
  const history = useQuery({
    queryKey: ["approvals", "history", status, page],
    queryFn: () => api<Page<Approval>>("/approvals", { query: { status, decided: true, page, page_size: 20 } }),
    placeholderData: keepPreviousData,
  });
  const [approving, setApproving] = React.useState<Approval | null>(null);
  const [rejecting, setRejecting] = React.useState<Approval | null>(null);
  const [reason, setReason] = React.useState("");
  React.useEffect(() => { if (!rejecting) setReason(""); }, [rejecting]);

  const refresh = () => qc.invalidateQueries({ queryKey: ["approvals"] });
  const approve = useMutation({
    mutationFn: (a: Approval) => run(() => api<Approval>(`/approvals/${a.id}/approve`, { method: "POST", body: {} })),
    onSuccess: (r) => {
      setApproving(null);
      refresh();
      qc.invalidateQueries({ queryKey: ["databases"] });
      qc.invalidateQueries({ queryKey: ["projects"] });
      if (r.status === "executed") toast.success("Approved", { description: "The operation has started." });
      else toast.error("Approved, but the operation couldn't run", { description: (r.result as { message?: string } | null)?.message ?? undefined });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const reject = useMutation({
    mutationFn: (a: Approval) => api<Approval>(`/approvals/${a.id}/reject`, { method: "POST", body: { reason: reason.trim() || null } }),
    onSuccess: () => { setRejecting(null); refresh(); toast.success("Request rejected", { description: "Nothing was changed." }); },
    onError: (e) => toast.error(errorMessage(e)),
  });

  React.useEffect(() => {
    if (focus && pending.data) document.getElementById(`approval-${focus}`)?.scrollIntoView({ block: "center" });
  }, [focus, pending.data]);

  return (
    <>
      <PageHeader
        title="Approvals"
        description="Deletions and password rotations requested by agent keys (or keys you marked as needing approval) wait here until you decide. Requests expire after 24 hours."
      />
      <section aria-labelledby="pending-h" className="mb-8">
        <h2 id="pending-h" className="mb-3 flex items-center gap-2 text-sm font-semibold">
          Waiting for you {pending.data && pending.data.total > 0 && <Badge variant="warning">{pending.data.total}</Badge>}
        </h2>
        {!pending.data ? <Skeleton className="h-28" /> : pending.data.items.length === 0 ? (
          <Card>
            <EmptyState icon={ShieldCheck} title="Nothing to approve" description="When an agent asks to delete or rotate something, it appears here and you get an email."
              action={<Button asChild variant="secondary"><Link href="/agents">Manage agents</Link></Button>} />
          </Card>
        ) : (
          <div className="space-y-3">
            {pending.data.items.map((a) => (
              <PendingCard key={a.id} a={a} highlighted={a.id === focus} onApprove={() => setApproving(a)} onReject={() => setRejecting(a)} />
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="history-h">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 id="history-h" className="text-sm font-semibold">History</h2>
          <NativeSelect aria-label="Filter by outcome" className="w-44" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All outcomes</option>
            <option value="executed">Approved · done</option>
            <option value="failed">Approved · failed</option>
            <option value="rejected">Rejected</option>
            <option value="expired">Expired</option>
          </NativeSelect>
        </div>
        <Card>
          {!history.data ? <div className="p-5"><Skeleton className="h-32" /></div> : history.data.items.length === 0 ? (
            <EmptyState icon={Clock} title="No decisions yet" description="Approved, rejected and expired requests are kept here." />
          ) : (
            <Table>
              <THead><TR><TH>Request</TH><TH>Key</TH><TH>Outcome</TH><TH>Requested</TH><TH>Decided</TH></TR></THead>
              <TBody>
                {history.data.items.map((a) => (
                  <TR key={a.id}>
                    <TD>
                      <div className="font-medium">{a.action_label}</div>
                      <div className="max-w-md truncate text-xs text-muted" title={a.summary}>{a.summary}</div>
                      {a.status === "failed" && a.result && <div className="text-xs text-critical">{String((a.result as { message?: string }).message ?? "")}</div>}
                      {a.decision_reason && <div className="text-xs text-subtle">Note: {a.decision_reason}</div>}
                    </TD>
                    <TD className="text-muted">{a.api_key_name ?? "—"}</TD>
                    <TD><StatusBadge status={a.status} /></TD>
                    <TD className="whitespace-nowrap text-muted">{formatDateTime(a.created_at)}</TD>
                    <TD className="whitespace-nowrap text-muted">{a.decided_at ? formatDateTime(a.decided_at) : "—"}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Card>
        {history.data && <Pagination page={page} pageSize={20} total={history.data.total} onPage={setPage} />}
      </section>

      <ConfirmDialog
        open={!!approving}
        onOpenChange={(o) => !o && setApproving(null)}
        title={`Approve: ${approving?.action_label.toLowerCase() ?? ""}?`}
        description={<>
          <span className="block first-letter:uppercase">{approving?.summary}.</span>
          <span className="mt-2 block">It runs right away, as you, with the usual checks. It can&apos;t be undone.</span>
        </>}
        confirmLabel="Approve and run"
        loading={approve.isPending}
        onConfirm={() => approving && approve.mutate(approving)}
      />
      <Dialog open={!!rejecting} onOpenChange={(o) => !o && setRejecting(null)}>
        <DialogContent title="Reject this request?" description="Nothing is changed. The agent sees the rejection and your note when it checks the request.">
          <form onSubmit={(e) => { e.preventDefault(); if (rejecting) reject.mutate(rejecting); }}>
            <FormField id="reject-reason" label="Note for the agent" hint="Optional">
              <Input id="reject-reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} placeholder="Keep this database, it's used by staging" />
            </FormField>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setRejecting(null)}>Cancel</Button>
              <Button type="submit" loading={reject.isPending}>Reject request</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      {dialog}
    </>
  );
}

export default function ApprovalsPage() {
  return (
    <Suspense>
      <ApprovalsInner />
    </Suspense>
  );
}
