"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";
import type { Project } from "@/lib/types";

export function EditProjectDialog({ project, open, onOpenChange }: { project: Project; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [name, setName] = React.useState(project.name);
  const [env, setEnv] = React.useState(project.environment);
  const [desc, setDesc] = React.useState(project.description ?? "");
  React.useEffect(() => {
    if (open) { setName(project.name); setEnv(project.environment); setDesc(project.description ?? ""); }
  }, [open, project]);
  const save = useMutation({
    mutationFn: () => api<Project>(`/projects/${project.id}`, { method: "PATCH", body: { name, environment: env, description: desc } }),
    onSuccess: () => { toast.success("Project updated"); qc.invalidateQueries({ queryKey: ["projects"] }); onOpenChange(false); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Edit project">
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <FormField id="edit-name" label="Name"><Input id="edit-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={64} /></FormField>
          <FormField id="edit-env" label="Environment">
            <NativeSelect id="edit-env" value={env} onChange={(e) => setEnv(e.target.value)}>
              <option value="development">Development</option><option value="staging">Staging</option><option value="production">Production</option>
            </NativeSelect>
          </FormField>
          <FormField id="edit-desc" label="Description" optional><Textarea id="edit-desc" rows={2} value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={500} /></FormField>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" loading={save.isPending}>Save</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function useProjectActions(project: Project | undefined, onDeleted?: () => void) {
  const qc = useQueryClient();
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const archive = useMutation({
    mutationFn: (archived: boolean) => api(`/projects/${project!.id}/${archived ? "archive" : "unarchive"}`, { method: "POST" }),
    onSuccess: (_d, archived) => { toast.success(archived ? "Project archived" : "Project restored"); qc.invalidateQueries({ queryKey: ["projects"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: () => api(`/projects/${project!.id}`, { method: "DELETE", query: { confirm: project!.name } }),
    onSuccess: () => { toast.success("Project deleted"); setConfirmDelete(false); qc.invalidateQueries({ queryKey: ["projects"] }); onDeleted?.(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const deleteDialog = project ? (
    <ConfirmDialog
      open={confirmDelete}
      onOpenChange={setConfirmDelete}
      title={`Delete project “${project.name}”?`}
      description={project.database_count > 0 ? `This project still has ${project.database_count} database(s). Delete them first.` : "The project will be removed. It has no databases."}
      confirmText={project.database_count > 0 ? undefined : project.name}
      confirmLabel="Delete project"
      loading={del.isPending}
      onConfirm={() => (project.database_count > 0 ? setConfirmDelete(false) : del.mutate())}
    />
  ) : null;
  return { archive, askDelete: () => setConfirmDelete(true), deleteDialog };
}
