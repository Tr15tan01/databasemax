"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { FormField } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useReauth } from "@/features/app/reauth-dialog";
import { api, errorMessage } from "@/lib/api";
import type { Database, Options } from "@/lib/types";
import { formatMB } from "@/lib/utils";

export function SettingsTab({ database: d }: { database: Database }) {
  const qc = useQueryClient();
  const router = useRouter();
  const { run, dialog } = useReauth();
  const options = useQuery({ queryKey: ["db-options"], queryFn: () => api<Options>("/databases/options") });
  const [name, setName] = React.useState(d.name);
  const [env, setEnv] = React.useState(d.environment);
  const [storage, setStorage] = React.useState(d.allocated_storage_mb);
  const [conns, setConns] = React.useState(d.max_connections);
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const [confirmStop, setConfirmStop] = React.useState(false);
  const [finalBackup, setFinalBackup] = React.useState(true);
  React.useEffect(() => { setName(d.name); setEnv(d.environment); setStorage(d.allocated_storage_mb); setConns(d.max_connections); }, [d.name, d.environment, d.allocated_storage_mb, d.max_connections]);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["databases"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); };

  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Database>(`/databases/${d.id}`, { method: "PATCH", body }),
    onSuccess: () => { toast.success("Settings saved"); refresh(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const power = useMutation({
    mutationFn: (action: "stop" | "start") => api(`/databases/${d.id}/${action}`, { method: "POST" }),
    onSuccess: (_r, action) => { setConfirmStop(false); toast.success(action === "stop" ? "Stopping database" : "Starting database"); refresh(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: () => run(() => api(`/databases/${d.id}`, { method: "DELETE", query: { confirm: d.name, final_backup: canFinal && finalBackup } })),
    onSuccess: () => {
      toast.success("Deleting database", {
        description: canFinal && finalBackup
          ? "Taking a final encrypted backup first. You'll find it under Backups."
          : "The instance, storage and credentials are being removed.",
      });
      refresh();
      router.push("/databases");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const canFinal = d.status === "ready";
  const o = options.data;
  const storageMax = o ? d.allocated_storage_mb + (o.storage.remaining_mb ?? 0) : d.allocated_storage_mb;
  const storageMaxPerDb = o?.storage.max_per_database_mb ?? storageMax;
  return (
    <div className="space-y-4">
      {dialog}
      <Card>
        <CardHeader><div><CardTitle>General</CardTitle><CardDescription>The display name doesn&apos;t change your connection URL.</CardDescription></div></CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <FormField id="set-name" label="Name"><Input id="set-name" value={name} maxLength={64} onChange={(e) => setName(e.target.value)} /></FormField>
          <FormField id="set-env" label="Environment label">
            <NativeSelect id="set-env" value={env} onChange={(e) => setEnv(e.target.value)}>
              <option value="development">Development</option><option value="staging">Staging</option><option value="production">Production</option>
            </NativeSelect>
          </FormField>
        </CardContent>
        <CardFooter className="justify-end">
          <Button size="sm" disabled={name === d.name && env === d.environment} loading={save.isPending} onClick={() => save.mutate({ name, environment: env })}>Save</Button>
        </CardFooter>
      </Card>
      <Card>
        <CardHeader><div><CardTitle>Resources</CardTitle><CardDescription>Adjust within your plan&apos;s limits. Compute size changes aren&apos;t available yet.</CardDescription></div></CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <FormField id="set-storage" label="Storage allocation (MB)" hint={`Up to ${formatMB(Math.min(storageMax, storageMaxPerDb))} with your remaining plan storage.`}>
            <Input id="set-storage" type="number" min={256} step={64} max={Math.min(storageMax, storageMaxPerDb)} value={storage} onChange={(e) => setStorage(Number(e.target.value))} />
          </FormField>
          <FormField id="set-conns" label="Connection limit" hint={o ? `Your plan allows up to ${o.max_connections_per_db}.` : undefined}>
            <Input id="set-conns" type="number" min={1} max={o?.max_connections_per_db} value={conns} onChange={(e) => setConns(Number(e.target.value))} disabled={d.status !== "ready"} />
          </FormField>
        </CardContent>
        <CardFooter className="justify-end">
          <Button size="sm" disabled={storage === d.allocated_storage_mb && conns === d.max_connections} loading={save.isPending}
            onClick={() => save.mutate({ ...(storage !== d.allocated_storage_mb && { storage_mb: storage }), ...(conns !== d.max_connections && { max_connections: conns }) })}>Save</Button>
        </CardFooter>
      </Card>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>{d.status === "stopped" ? "Start database" : "Stop database"}</CardTitle>
            <CardDescription>{d.status === "stopped" ? "Start the instance again. Your data and credentials are unchanged." : "Stopping closes all connections and refuses new ones. Data stays on the volume."}</CardDescription>
          </div>
          {d.status === "stopped" ? (
            <Button variant="secondary" size="sm" onClick={() => power.mutate("start")} loading={power.isPending} disabled={d.suspended_by_admin}><Play /> Start</Button>
          ) : (
            <Button variant="secondary" size="sm" onClick={() => setConfirmStop(true)} disabled={d.status !== "ready"}><Pause /> Stop</Button>
          )}
        </CardHeader>
        {d.suspended_by_admin && <CardContent className="pt-0 text-[13px] text-critical">This database was suspended by DatabaseMax support. Contact support to restore it.</CardContent>}
      </Card>
      <Card className="border-critical/30">
        <CardHeader>
          <div>
            <CardTitle>Delete database</CardTitle>
            <CardDescription>Permanently removes the instance, its storage and credentials. Existing backups are kept until their retention ends and can be restored from the Backups page.</CardDescription>
          </div>
          <Button variant="danger-outline" size="sm" onClick={() => setConfirmDelete(true)} disabled={d.status === "deleting"}><Trash2 /> Delete</Button>
        </CardHeader>
      </Card>
      <ConfirmDialog open={confirmStop} onOpenChange={setConfirmStop} destructive={false} title={`Stop “${d.name}”?`}
        description="Open connections will be closed and new connections refused until you start it again." confirmLabel="Stop database"
        loading={power.isPending} onConfirm={() => power.mutate("stop")} />
      <ConfirmDialog open={confirmDelete} onOpenChange={setConfirmDelete} title={`Delete “${d.name}”?`}
        description="All data in this database will be permanently destroyed and its connection URL will stop working."
        confirmText={d.name} confirmLabel="Delete database" loading={del.isPending} onConfirm={() => del.mutate()}>
        <label htmlFor="final-backup" className="mb-4 flex items-start justify-between gap-4 rounded-lg border border-border px-3 py-2.5">
          <span className="text-[13px]">
            <span className="block font-medium">Take a final backup first</span>
            <span className="text-muted">
              {canFinal
                ? "Kept for your plan's retention period; you can restore it into a new database later."
                : "Only available while the database is running. Existing backups are kept either way."}
            </span>
          </span>
          <Switch id="final-backup" checked={canFinal && finalBackup} disabled={!canFinal} onCheckedChange={setFinalBackup} />
        </label>
      </ConfirmDialog>
    </div>
  );
}
