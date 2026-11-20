"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Server } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CodeBlock } from "@/components/ui/code-block";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Meter } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";
import { Tooltip } from "@/components/ui/tooltip";
import { useReauth } from "@/features/app/reauth-dialog";
import { ReasonDialog } from "@/features/admin/reason-dialog";
import { api, errorMessage } from "@/lib/api";
import { formatMB, pct, relativeTime } from "@/lib/utils";

interface Host {
  id: string; name: string; provider: string; region: string; status: string; status_reason: string | null;
  status_changed_at: string | null; endpoint: string; labels: Record<string, string>; last_heartbeat_at: string | null;
  heartbeat_fresh: boolean; last_error: string | null; token_registered: boolean;
  capacity: { memory_budget_mb?: number; disk_budget_mb?: number; max_instances?: number; storage_backend?: string; listen_ip?: string | null; runtime?: string };
  allocated: { instances: number; memory_mb: number; cpus: number; storage_mb: number };
  databases: { total: number; by_status: Record<string, number> };
}
type Action = "drain" | "undrain" | "retire";

const COPY: Record<Action, { title: string; description: string; label: string }> = {
  drain: { title: "Drain this host?", description: "No new databases or clones will be placed here. Existing databases keep running.", label: "Drain" },
  undrain: { title: "Resume placements?", description: "The host becomes eligible for new databases again.", label: "Undrain" },
  retire: { title: "Retire this host?", description: "Only possible when no databases remain on it. It stops being monitored.", label: "Retire" },
};

function Row({ label, used, total, unit }: { label: string; used: number; total?: number; unit: (n: number) => string }) {
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs"><span className="text-muted">{label}</span><span className="tabular">{unit(used)}{total ? ` / ${unit(total)}` : ""}</span></div>
      <Meter value={total ? pct(used, total) : 0} label={label} />
    </div>
  );
}

export default function AdminHosts() {
  const qc = useQueryClient();
  const { run, dialog } = useReauth();
  const [pending, setPending] = React.useState<{ host: Host; action: Action } | null>(null);
  const q = useQuery({ queryKey: ["admin", "hosts"], queryFn: () => api<{ items: Host[] }>("/admin/hosts"), refetchInterval: 15_000 });
  const act = useMutation({
    mutationFn: ({ host, action, reason }: { host: Host; action: Action; reason: string }) =>
      run(() => api(`/admin/hosts/${host.id}/status`, { method: "POST", body: { action, reason } })),
    onSuccess: () => { toast.success("Host updated — recorded in the audit log"); setPending(null); qc.invalidateQueries({ queryKey: ["admin"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const hosts = q.data?.items.filter((h) => h.status !== "retired");
  return (
    <>
      {dialog}
      <PageHeader title="Hosts" description="Data-plane hosts. New databases go to the active host with the most headroom in their region; databases never move between hosts unless an operator moves them." />
      {!hosts ? <Skeleton className="h-64" /> : (
        <div className="grid gap-4 lg:grid-cols-2">
          {hosts.map((h) => (
            <Card key={h.id}>
              <CardHeader>
                <div>
                  <CardTitle className="flex items-center gap-2"><Server className="size-4" /> {h.name}</CardTitle>
                  <CardDescription>
                    {h.region} · {h.capacity.storage_backend === "loop" ? "hard storage limits" : "soft storage limits"}
                    {h.capacity.listen_ip && h.capacity.listen_ip !== "127.0.0.1" ? ` · instances on ${h.capacity.listen_ip}` : " · instances on loopback"}
                  </CardDescription>
                </div>
                <StatusBadge status={h.status} />
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap gap-2 text-xs">
                  <Tooltip content={h.last_heartbeat_at ?? "never"}>
                    <span><Badge variant={h.heartbeat_fresh ? "good" : "critical"}>heartbeat {relativeTime(h.last_heartbeat_at)}</Badge></span>
                  </Tooltip>
                  <Badge variant="outline">{h.databases.total} database{h.databases.total === 1 ? "" : "s"}</Badge>
                  {!h.token_registered && <Badge variant="neutral">bootstrap (env token)</Badge>}
                  {Object.entries(h.labels ?? {}).map(([k, v]) => <Badge key={k} variant="outline">{k}={v}</Badge>)}
                </div>
                {h.status_reason && <p className="text-xs text-muted">{h.status_reason}{h.status_changed_at ? ` · ${relativeTime(h.status_changed_at)}` : ""}</p>}
                {h.last_error && <p className="text-xs text-critical">Last error: {h.last_error}</p>}
                <Row label="Memory reserved" used={h.allocated.memory_mb} total={h.capacity.memory_budget_mb} unit={formatMB} />
                <Row label="Storage reserved" used={h.allocated.storage_mb} total={h.capacity.disk_budget_mb} unit={formatMB} />
                <Row label="Instances" used={h.allocated.instances} total={h.capacity.max_instances} unit={(n) => String(n)} />
                <p className="truncate font-mono text-[11px] text-subtle">{h.endpoint}</p>
                <div className="flex gap-2 pt-1">
                  {h.status === "active" && <Button size="sm" variant="secondary" onClick={() => setPending({ host: h, action: "drain" })}>Drain</Button>}
                  {h.status === "offline" && <Button size="sm" variant="secondary" onClick={() => setPending({ host: h, action: "drain" })}>Drain when back</Button>}
                  {h.status === "draining" && <Button size="sm" onClick={() => setPending({ host: h, action: "undrain" })}>Undrain</Button>}
                  {h.databases.total === 0 && h.status !== "active" && <Button size="sm" variant="danger-outline" onClick={() => setPending({ host: h, action: "retire" })}>Retire</Button>}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      <Card className="mt-6">
        <CardHeader><div><CardTitle>Adding a host</CardTitle><CardDescription>Hosts are registered from the operator CLI so their provisioner token never passes through a browser.</CardDescription></div></CardHeader>
        <CardContent>
          <CodeBlock lang="bash" wrap code={`# on the new worker VM:
sudo infra/scripts/host-setup.sh --role worker
sudo infra/scripts/init-env.sh --worker --host-name worker-2 --private-ip 10.0.1.12 --gateway-ip 10.0.1.11
sudo infra/scripts/deploy.sh local --role worker
# on the control-plane VM (token from the worker's .env, read from the environment, not argv):
sudo DMX_HOST_TOKEN=... dmx-admin register-host \\
  --name worker-2 --endpoint http://10.0.1.12:8090 --region <region>
sudo dmx-admin firewall-plan --host worker-2 --control-ip 10.0.1.11 --gateway-ip 10.0.1.11`} />
        </CardContent>
      </Card>
      {pending && (
        <ReasonDialog open onOpenChange={(o) => !o && setPending(null)} title={COPY[pending.action].title} description={COPY[pending.action].description}
          confirmLabel={COPY[pending.action].label} destructive={pending.action === "retire"} loading={act.isPending}
          onConfirm={(reason) => act.mutate({ ...pending, reason })} />
      )}
    </>
  );
}
