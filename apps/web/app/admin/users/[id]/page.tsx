"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, LogOut, MailCheck, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Textarea } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ReasonDialog } from "@/features/admin/reason-dialog";
import { useReauth } from "@/features/app/reauth-dialog";
import { api, errorMessage } from "@/lib/api";
import { ADMIN_ACTOR_LABELS, actionLabel } from "@/lib/labels";
import type { AuditEvent, Database } from "@/lib/types";
import { formatBytes, formatDate, formatDateTime, formatMB } from "@/lib/utils";

interface Detail {
  user: { id: string; email: string; display_name: string | null; status: string; suspended_reason: string | null; verified: boolean; created_at: string; last_login_at: string | null; has_password: boolean; providers: string[] };
  subscription: { plan: string; effective_plan: string; status: string; period_end: string | null; paddle_customer_id: string | null; cancel_at_period_end: boolean; grace_until: string | null };
  usage: { projects: number; databases: number; allocated_storage_mb: number; storage_used_bytes: number };
  limits: Record<string, unknown>;
  active_sessions: number;
  active_api_keys: number;
  projects: { id: string; name: string; environment: string; status: string; created_at: string }[];
  databases: Database[];
  notes: { id: string; body: string; author: string | null; created_at: string }[];
  logins: { success: boolean; reason: string | null; method: string; ip: string | null; created_at: string }[];
  audit: AuditEvent[];
}

type Action = "suspend" | "reactivate" | "revoke-sessions" | "verify-email";

export default function AdminUserDetail() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { run, dialog } = useReauth();
  const [action, setAction] = React.useState<Action | null>(null);
  const [note, setNote] = React.useState("");
  const q = useQuery({ queryKey: ["admin", "user", id], queryFn: () => api<Detail>(`/admin/users/${id}`) });
  const act = useMutation({
    mutationFn: ({ a, reason }: { a: Action; reason: string }) => run(() => api<{ message?: string }>(`/admin/users/${id}/${a}`, { method: "POST", body: { reason } })),
    onSuccess: () => { toast.success("Done — recorded in the audit log"); setAction(null); qc.invalidateQueries({ queryKey: ["admin"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const addNote = useMutation({
    mutationFn: () => api(`/admin/users/${id}/notes`, { method: "POST", body: { body: note } }),
    onSuccess: () => { setNote(""); qc.invalidateQueries({ queryKey: ["admin", "user", id] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  if (!d) return <Skeleton className="h-80" />;
  const u = d.user;
  const titles: Record<Action, [string, string, boolean]> = {
    suspend: ["Suspend this account?", "Signs the user out everywhere and blocks sign-in, API keys and database connections.", true],
    reactivate: ["Reactivate this account?", "Restores sign-in, API access and database connections.", false],
    "revoke-sessions": ["Sign this user out everywhere?", "All browser sessions are revoked. API keys are unaffected.", true],
    "verify-email": ["Mark this email as verified?", "Skips the email confirmation for this account (for test or support cases). The account can then create databases.", false],
  };
  return (
    <>
      {dialog}
      <PageHeader
        eyebrow={<Link href="/admin/users" className="hover:text-foreground">Users</Link>}
        title={<span className="flex items-center gap-3">{u.email} <StatusBadge status={u.status} /></span>}
        description={`${u.display_name ?? "No display name"} · joined ${formatDate(u.created_at)} · ${u.verified ? "email verified" : "email NOT verified"}`}
        actions={
          <>
            {!u.verified && <Button variant="secondary" onClick={() => setAction("verify-email")}><MailCheck /> Mark email verified</Button>}
            <Button variant="secondary" onClick={() => setAction("revoke-sessions")}><LogOut /> Revoke sessions</Button>
            {u.status === "suspended" ? (
              <Button onClick={() => setAction("reactivate")}><RotateCcw /> Reactivate</Button>
            ) : (
              <Button variant="danger" onClick={() => setAction("suspend")}><Ban /> Suspend</Button>
            )}
          </>
        }
      />
      {u.suspended_reason && <p className="mb-4 rounded-md bg-critical-soft px-3 py-2 text-[13px] text-critical">Suspended: {u.suspended_reason}</p>}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>Account</CardTitle></CardHeader>
          <CardContent>
            <dl className="grid grid-cols-[130px_1fr] gap-y-2 text-[13px]">
              <dt className="text-muted">User ID</dt><dd className="truncate font-mono text-xs">{u.id}</dd>
              <dt className="text-muted">Last sign-in</dt><dd>{formatDateTime(u.last_login_at)}</dd>
              <dt className="text-muted">Sign-in methods</dt><dd>{[u.has_password && "password", ...u.providers].filter(Boolean).join(", ") || "—"}</dd>
              <dt className="text-muted">Active sessions</dt><dd>{d.active_sessions}</dd>
              <dt className="text-muted">Active API keys</dt><dd>{d.active_api_keys}</dd>
            </dl>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Plan & billing</CardTitle></CardHeader>
          <CardContent>
            <dl className="grid grid-cols-[130px_1fr] gap-y-2 text-[13px]">
              <dt className="text-muted">Plan</dt><dd className="capitalize">{d.subscription.plan}{d.subscription.effective_plan !== d.subscription.plan && <Badge variant="warning" className="ml-2">effective: {d.subscription.effective_plan}</Badge>}</dd>
              <dt className="text-muted">Status</dt><dd>{d.subscription.status}{d.subscription.cancel_at_period_end && " · cancels at period end"}</dd>
              <dt className="text-muted">Period end</dt><dd>{formatDate(d.subscription.period_end)}</dd>
              <dt className="text-muted">Grace until</dt><dd>{formatDate(d.subscription.grace_until)}</dd>
              <dt className="text-muted">Paddle customer</dt><dd className="font-mono text-xs">{d.subscription.paddle_customer_id ?? "—"}</dd>
            </dl>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Usage</CardTitle></CardHeader>
          <CardContent>
            <dl className="grid grid-cols-[130px_1fr] gap-y-2 text-[13px]">
              <dt className="text-muted">Projects</dt><dd>{d.usage.projects} / {String(d.limits.max_projects)}</dd>
              <dt className="text-muted">Databases</dt><dd>{d.usage.databases} / {String(d.limits.max_databases)}</dd>
              <dt className="text-muted">Allocated</dt><dd>{formatMB(d.usage.allocated_storage_mb)} / {formatMB(Number(d.limits.max_total_storage_mb))}</dd>
              <dt className="text-muted">Storage used</dt><dd>{formatBytes(d.usage.storage_used_bytes)} (measured)</dd>
            </dl>
          </CardContent>
        </Card>
      </div>
      <Card className="mt-4">
        <CardHeader><CardTitle>Databases</CardTitle></CardHeader>
        <Table>
          <THead><TR><TH>Name</TH><TH>Project</TH><TH>Status</TH><TH>Size</TH><TH>Storage</TH><TH>Created</TH></TR></THead>
          <TBody>
            {d.databases.map((db) => (
              <TR key={db.id}>
                <TD><Link href={`/admin/databases/${db.id}`} className="font-medium hover:underline">{db.name}</Link></TD>
                <TD className="text-muted">{db.project_name}</TD>
                <TD><StatusBadge status={db.status} /></TD>
                <TD className="capitalize text-muted">{db.compute_profile}</TD>
                <TD className="tabular text-muted">{formatBytes(db.storage_used_bytes)} / {formatMB(db.allocated_storage_mb)}</TD>
                <TD className="text-muted">{formatDate(db.created_at)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
        {d.databases.length === 0 && <p className="p-5 text-[13px] text-subtle">No databases.</p>}
      </Card>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Internal notes</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (note.trim()) addNote.mutate(); }}>
              <Textarea aria-label="New note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note visible to operators only…" />
              <Button type="submit" variant="secondary" loading={addNote.isPending} disabled={!note.trim()}>Add</Button>
            </form>
            <ul className="space-y-3">
              {d.notes.map((n) => (
                <li key={n.id} className="rounded-md border border-border p-3 text-[13px]">
                  <p className="whitespace-pre-wrap">{n.body}</p>
                  <p className="mt-1 text-xs text-subtle">{n.author ?? "system"} · {formatDateTime(n.created_at)}</p>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Recent sign-ins</CardTitle></CardHeader>
          <Table>
            <THead><TR><TH>Result</TH><TH>Method</TH><TH>IP</TH><TH>When</TH></TR></THead>
            <TBody>
              {d.logins.map((l, i) => (
                <TR key={i}>
                  <TD>{l.success ? <Badge variant="good">success</Badge> : <Badge variant="critical">{l.reason ?? "failed"}</Badge>}</TD>
                  <TD className="text-muted">{l.method}</TD>
                  <TD className="font-mono text-xs text-muted">{l.ip ?? "—"}</TD>
                  <TD className="text-muted">{formatDateTime(l.created_at)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </Card>
      </div>
      <Card className="mt-4">
        <CardHeader><CardTitle>Audit history</CardTitle></CardHeader>
        <Table>
          <THead><TR><TH>Action</TH><TH>Actor</TH><TH>Reason</TH><TH>When</TH></TR></THead>
          <TBody>
            {d.audit.map((e) => (
              <TR key={e.id}>
                <TD className={e.outcome === "failure" ? "text-critical" : ""}>{actionLabel(e.action)} <span className="font-mono text-[11px] text-subtle">{e.action}</span></TD>
                <TD><Badge variant="outline">{ADMIN_ACTOR_LABELS[e.actor_type] ?? e.actor_type}</Badge></TD>
                <TD className="max-w-sm text-muted">{e.reason ?? "—"}</TD>
                <TD className="whitespace-nowrap text-muted">{formatDateTime(e.created_at)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </Card>
      {action && (
        <ReasonDialog open onOpenChange={(o) => !o && setAction(null)} title={titles[action][0]} description={titles[action][1]} destructive={titles[action][2]}
          confirmLabel="Confirm" loading={act.isPending} onConfirm={(reason) => act.mutate({ a: action, reason })} />
      )}
    </>
  );
}
