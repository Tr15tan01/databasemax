"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Bot, ChevronDown, ChevronUp, Plus, ShieldCheck, Zap } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { TimeSeriesChart } from "@/components/charts/time-series";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { CodeBlock } from "@/components/ui/code-block";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Stat } from "@/components/ui/stat";
import { CreateKeyDialog, NewKeyDialog } from "@/features/api-keys/create-key-dialog";
import { api } from "@/lib/api";
import { actionLabel } from "@/lib/labels";
import type { ApiKeyCreated, AuditEvent, KeyActivity, Page } from "@/lib/types";
import { formatDateTime, formatNumber, relativeTime } from "@/lib/utils";

const HANDLING = `# Destructive calls made with an agent key answer 202 and wait for you:
curl -X DELETE "$API/v1/databases/$DB?confirm=my-db" -H "Authorization: Bearer $DMX_AGENT_KEY"
# {"status": "pending_approval", "approval": {"id": "…", "status": "pending", …}}

# The agent polls its request until you decide (executed | rejected | expired | failed):
curl "$API/v1/approvals/$APPROVAL_ID" -H "Authorization: Bearer $DMX_AGENT_KEY"`;

function hourlyPoints(hourly: number[]): { t: string; v: number }[] {
  const now = new Date();
  now.setMinutes(0, 0, 0);
  return hourly.map((v, i) => ({ t: new Date(now.getTime() - (hourly.length - 1 - i) * 3_600_000).toISOString(), v }));
}

function RecentActions({ keyId }: { keyId: string }) {
  const q = useQuery({
    queryKey: ["activity", "key", keyId],
    queryFn: () => api<Page<AuditEvent>>("/activity", { query: { api_key_id: keyId, page_size: 8 } }),
  });
  if (!q.data) return <Skeleton className="h-20" />;
  if (q.data.items.length === 0) return <p className="text-[13px] text-muted">This key hasn&apos;t changed anything yet. Read-only requests are counted above but not listed.</p>;
  return (
    <ul className="divide-y divide-border text-[13px]">
      {q.data.items.map((e) => (
        <li key={e.id} className="flex items-center justify-between gap-3 py-2">
          <span className={e.outcome === "success" ? "" : "text-critical"}>{actionLabel(e.action)}{e.resource_type && <span className="text-muted"> · {e.resource_type}</span>}</span>
          <span className="shrink-0 text-xs text-muted" title={formatDateTime(e.created_at)}>{relativeTime(e.created_at)}</span>
        </li>
      ))}
    </ul>
  );
}

function AgentCard({ a }: { a: KeyActivity }) {
  const [open, setOpen] = React.useState(false);
  const k = a.key;
  const inactive = k.status !== "active";
  return (
    <Card className="p-5" data-testid="agent-card">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Bot className="size-4 text-accent" aria-hidden />
            <span className="text-[15px] font-semibold">{k.name}</span>
            <Badge variant={inactive ? "neutral" : "good"}>{k.status}</Badge>
            {a.pending_approvals > 0 && (
              <Link href="/approvals"><Badge variant="warning"><ShieldCheck aria-hidden /> {a.pending_approvals} waiting</Badge></Link>
            )}
          </div>
          {k.description && <p className="text-[13px] text-muted">{k.description}</p>}
          <p className="text-xs text-subtle">
            dmx_live_{k.prefix}_… · {k.scopes.length} scopes{k.project_ids ? " · project-restricted" : ""} · last used {k.last_used_at ? relativeTime(k.last_used_at) : "never"}
          </p>
          <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div><dt className="text-xs text-muted">Requests · 24 h</dt><dd className="text-lg font-semibold tabular">{formatNumber(a.requests_24h)}</dd></div>
            <div><dt className="text-xs text-muted">Requests · 7 days</dt><dd className="text-lg font-semibold tabular">{formatNumber(a.requests_7d)}</dd></div>
            <div><dt className="text-xs text-muted">Changes · 7 days</dt><dd className="text-lg font-semibold tabular">{formatNumber(a.actions_7d)}{a.failed_actions_7d > 0 && <span className="ml-1 text-xs font-normal text-critical">{a.failed_actions_7d} failed</span>}</dd></div>
            <div><dt className="text-xs text-muted">Last change</dt><dd className="truncate text-[13px] font-medium" title={a.last_action ?? ""}>{a.last_action ? actionLabel(a.last_action) : "—"}</dd>{a.last_action_at && <dd className="text-xs text-muted">{relativeTime(a.last_action_at)}</dd>}</div>
          </dl>
        </div>
        <div className="w-full lg:w-72">
          <div className="mb-1 text-xs text-muted">Requests per hour, last 24 h</div>
          <TimeSeriesChart data={hourlyPoints(a.hourly)} name="Requests" kind="bar" height={96} withTime hourlyTicks emptyMessage="No requests yet." />
        </div>
      </div>
      <div className="mt-3 border-t border-border pt-3">
        <Button variant="ghost" size="sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? <ChevronUp /> : <ChevronDown />} Recent changes
        </Button>
        {open && <div className="mt-2"><RecentActions keyId={k.id} /></div>}
      </div>
    </Card>
  );
}

export default function AgentsPage() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["api-keys", "activity", "agent"],
    queryFn: () => api<KeyActivity[]>("/api-keys/activity", { query: { kind: "agent" } }),
    refetchInterval: 60_000,
  });
  const [creating, setCreating] = React.useState(false);
  const [created, setCreated] = React.useState<ApiKeyCreated | null>(null);
  const active = q.data?.filter((a) => a.key.status === "active") ?? [];
  const totals = (q.data ?? []).reduce(
    (t, a) => ({ req: t.req + a.requests_24h, actions: t.actions + a.actions_7d, pending: t.pending + a.pending_approvals }),
    { req: 0, actions: 0, pending: 0 },
  );
  return (
    <>
      <PageHeader
        title="Agents"
        description="Machine identities for AI agents and automations. They use the same API, scopes, quotas and audit log as any key, and anything destructive waits for your approval."
        actions={<Button onClick={() => setCreating(true)}><Plus /> New agent key</Button>}
      />
      {!q.data ? <Skeleton className="h-40" /> : q.data.length === 0 ? (
        <div className="space-y-4">
          <Card>
            <EmptyState icon={Bot} title="No agents yet" description="Create an agent key for a coding assistant, a CI bot or any automation. You'll see its requests and changes here, and approve deletions before they happen."
              action={<Button onClick={() => setCreating(true)}><Plus /> New agent key</Button>} />
          </Card>
          <CodeBlock title="How agents handle approvals" code={HANDLING} lang="bash" />
        </div>
      ) : (
        <>
          <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Active agents" value={active.length} icon={Bot} />
            <Stat label="Requests · 24 h" value={formatNumber(totals.req)} icon={Zap} hint="Counted per key; updated every minute." />
            <Stat label="Changes · 7 days" value={formatNumber(totals.actions)} icon={Activity} hint="Audited actions performed with agent keys." />
            <Stat label="Waiting for approval" value={totals.pending} icon={ShieldCheck} sub={totals.pending > 0 ? <Link href="/approvals" className="text-accent hover:underline">Review now</Link> : "All clear"} />
          </div>
          <div className="space-y-3">{q.data.map((a) => <AgentCard key={a.key.id} a={a} />)}</div>
          <div className="mt-6"><CodeBlock title="How agents handle approvals" code={HANDLING} lang="bash" /></div>
        </>
      )}
      <CreateKeyDialog open={creating} defaultAgent onOpenChange={setCreating}
        onCreated={(k) => { setCreating(false); setCreated(k); qc.invalidateQueries({ queryKey: ["api-keys"] }); }} />
      <NewKeyDialog created={created} setCreated={setCreated} />
    </>
  );
}
