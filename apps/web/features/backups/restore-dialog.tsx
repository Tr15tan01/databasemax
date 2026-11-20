"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArchiveRestore, Copy } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";
import type { Backup, Database, Options, Page, Project } from "@/lib/types";
import { formatDateTime, formatMB } from "@/lib/utils";

function defaultName(source: string, when: string | null | undefined) {
  const d = when ? new Date(when) : new Date();
  const stamp = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
  return `${source}-restore-${stamp}`.slice(0, 64);
}

/**
 * Restore a backup (or copy the current data) into a NEW database. Nothing is overwritten.
 */
export function RestoreDialog({
  open,
  onOpenChange,
  source,
  backup,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  source: { id: string; name: string; project_id?: string; environment?: string; storage_mb?: number | null };
  backup: Backup | null;
}) {
  const qc = useQueryClient();
  const router = useRouter();
  const projects = useQuery({ queryKey: ["projects", "all"], queryFn: () => api<Page<Project>>("/projects", { query: { page_size: 100 } }), enabled: open });
  const options = useQuery({ queryKey: ["db-options"], queryFn: () => api<Options>("/databases/options"), enabled: open });
  const [name, setName] = React.useState("");
  const [projectId, setProjectId] = React.useState(source.project_id ?? "");
  const [env, setEnv] = React.useState(source.environment ?? "development");
  React.useEffect(() => {
    if (open) {
      setName(backup ? defaultName(source.name, backup.completed_at) : `${source.name}-copy`.slice(0, 64));
      setProjectId(source.project_id ?? "");
      setEnv(source.environment ?? "development");
    }
  }, [open, backup, source.name, source.project_id, source.environment]);
  React.useEffect(() => {
    if (!projectId && projects.data?.items[0]) setProjectId(projects.data.items[0].id);
  }, [projectId, projects.data]);

  const clone = useMutation({
    mutationFn: () =>
      api<Database>(`/databases/${source.id}/clone`, {
        method: "POST",
        body: { name: name.trim(), backup_id: backup?.id ?? null, project_id: projectId || null, environment: env },
      }),
    onSuccess: (d) => {
      toast.success(backup ? "Restoring into a new database" : "Copying into a new database", { description: "It gets its own connection URL once ready." });
      qc.invalidateQueries({ queryKey: ["databases"] });
      qc.invalidateQueries({ queryKey: ["backups"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onOpenChange(false);
      router.push(`/databases/${d.id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const title = backup ? "Restore to a new database" : "Copy into a new database";
  const description = backup
    ? `Creates a new database from the backup taken ${formatDateTime(backup.completed_at)}. “${source.name}” is not changed.`
    : `Takes a fresh, consistent backup of “${source.name}” and restores it into a new database. The source keeps running and is not changed.`;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={title} description={description}>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            clone.mutate();
          }}
        >
          <FormField id="restore-name" label="New database name">
            <Input id="restore-name" value={name} maxLength={64} required onChange={(e) => setName(e.target.value)} autoFocus />
          </FormField>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField id="restore-project" label="Project">
              <NativeSelect id="restore-project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                {(projects.data?.items ?? []).filter((p) => p.status === "active").map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </NativeSelect>
            </FormField>
            <FormField id="restore-env" label="Environment label">
              <NativeSelect id="restore-env" value={env} onChange={(e) => setEnv(e.target.value)}>
                <option value="development">Development</option>
                <option value="staging">Staging</option>
                <option value="production">Production</option>
              </NativeSelect>
            </FormField>
          </div>
          {options.data && source.storage_mb != null && (
            <p className={"rounded-lg border px-4 py-2.5 text-[13px] " + (source.storage_mb > (options.data.storage.remaining_mb ?? 0) ? "border-critical/30 bg-critical-soft text-critical" : "border-border text-muted")}>
              Estimate: allocates <span className="font-medium">{formatMB(source.storage_mb)}</span> of storage
              ({formatMB(options.data.storage.remaining_mb ?? 0)} remaining on your plan) and one database slot.
              {source.storage_mb > (options.data.storage.remaining_mb ?? 0) && " Not enough storage left — upgrade or free up storage first."}
            </p>
          )}
          <ul className="space-y-1 rounded-lg bg-surface-2 px-4 py-3 text-[13px] text-muted">
            <li>• Same PostgreSQL version, size and storage as the source; counts toward your plan&apos;s database quota.</li>
            <li>• New host, username and password — update your app&apos;s connection URL if you switch to it.</li>
            <li>• Tables, data, indexes, views, functions and sequences are restored; roles and grants from the source are not.</li>
          </ul>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" loading={clone.isPending} disabled={!name.trim() || !projectId}>
              {backup ? <ArchiveRestore /> : <Copy />} {backup ? "Restore" : "Create copy"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
