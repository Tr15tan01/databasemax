"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, KeyRound, Plus, ShieldCheck, Trash2 } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { CreateKeyDialog, NewKeyDialog } from "@/features/api-keys/create-key-dialog";
import { api, errorMessage } from "@/lib/api";
import type { ApiKey, ApiKeyCreated } from "@/lib/types";
import { formatDate, relativeTime } from "@/lib/utils";

export default function ApiKeysPage() {
  const qc = useQueryClient();
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: () => api<ApiKey[]>("/api-keys") });
  const [creating, setCreating] = React.useState(false);
  const [created, setCreated] = React.useState<ApiKeyCreated | null>(null);
  const [revoking, setRevoking] = React.useState<ApiKey | null>(null);
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/api-keys/${id}`, { method: "DELETE" }),
    onSuccess: () => { toast.success("API key revoked"); setRevoking(null); qc.invalidateQueries({ queryKey: ["api-keys"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <>
      <PageHeader title="API keys" description="Automate DatabaseMax with the HTTP API. Keys are scoped, can be limited to a project, and can be revoked at any time."
        actions={<Button onClick={() => setCreating(true)}><Plus /> Create API key</Button>} />
      <Card>
        {!keys.data ? <div className="p-5"><Skeleton className="h-24" /></div> : keys.data.length === 0 ? (
          <EmptyState icon={KeyRound} title="No API keys" description="Create a key to use the API from scripts, CI or your own tools." action={<Button onClick={() => setCreating(true)}><Plus /> Create API key</Button>} />
        ) : (
          <Table>
            <THead><TR><TH>Name</TH><TH>Key</TH><TH>Scopes</TH><TH>Last used</TH><TH>Expires</TH><TH>Status</TH><TH /></TR></THead>
            <TBody>
              {keys.data.map((k) => (
                <TR key={k.id}>
                  <TD className="font-medium">
                    <div className="flex flex-wrap items-center gap-1.5">{k.name}
                      {k.kind === "agent" && <Badge variant="accent"><Bot aria-hidden /> Agent</Badge>}
                      {k.requires_approval && <Badge variant="outline"><ShieldCheck aria-hidden /> Approval</Badge>}
                    </div>
                    {k.project_ids && <div className="text-xs text-subtle">Project-restricted</div>}
                  </TD>
                  <TD className="font-mono text-xs text-muted">dmx_live_{k.prefix}_…</TD>
                  <TD><div className="flex max-w-xs flex-wrap gap-1">{k.scopes.map((s) => <Badge key={s} variant="outline" className="font-mono text-[10px]">{s}</Badge>)}</div></TD>
                  <TD className="text-muted">{k.last_used_at ? relativeTime(k.last_used_at) : "Never"}</TD>
                  <TD className="text-muted">{k.expires_at ? formatDate(k.expires_at) : "Never"}</TD>
                  <TD><Badge variant={k.status === "active" ? "good" : "neutral"}>{k.status}</Badge></TD>
                  <TD className="text-right">{k.status === "active" && <Button variant="ghost" size="icon-sm" aria-label={`Revoke ${k.name}`} onClick={() => setRevoking(k)}><Trash2 /></Button>}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Card>
      <CreateKeyDialog open={creating} onOpenChange={setCreating} onCreated={(k) => { setCreating(false); setCreated(k); qc.invalidateQueries({ queryKey: ["api-keys"] }); }} />
      <NewKeyDialog created={created} setCreated={setCreated} />
      <ConfirmDialog open={!!revoking} onOpenChange={(o) => !o && setRevoking(null)} title={`Revoke “${revoking?.name}”?`}
        description="Requests using this key will fail immediately." confirmLabel="Revoke key" loading={revoke.isPending} onConfirm={() => revoking && revoke.mutate(revoking.id)} />
    </>
  );
}
