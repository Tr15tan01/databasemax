"use client";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Bot } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { api, errorMessage } from "@/lib/api";
import type { ApiKeyCreated, Page, Project } from "@/lib/types";

const PRESETS: Record<string, string[]> = {
  "Read only": ["projects:read", "databases:read", "backups:read", "usage:read", "activity:read"],
  "Provisioning": ["projects:read", "projects:write", "databases:read", "databases:write", "credentials:read"],
  "Full access": ["projects:read", "projects:write", "projects:delete", "databases:read", "databases:write", "databases:delete", "credentials:read", "credentials:rotate", "backups:read", "backups:write", "usage:read", "activity:read"],
};

export function CreateKeyDialog({ open, onOpenChange, onCreated, defaultAgent = false }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (k: ApiKeyCreated) => void; defaultAgent?: boolean }) {
  const scopes = useQuery({ queryKey: ["scopes"], queryFn: () => api<{ scope: string; description: string }[]>("/api-keys/scopes") });
  const projects = useQuery({ queryKey: ["projects", "for-keys"], queryFn: () => api<Page<Project>>("/projects", { query: { page_size: 100 } }) });
  const [name, setName] = React.useState("");
  const [selected, setSelected] = React.useState<string[]>(PRESETS["Read only"]!);
  const [project, setProject] = React.useState("");
  const [expires, setExpires] = React.useState("90");
  const [agent, setAgent] = React.useState(defaultAgent);
  const [approval, setApproval] = React.useState(defaultAgent);
  const [description, setDescription] = React.useState("");
  React.useEffect(() => { if (open) { setName(""); setSelected(PRESETS["Read only"]!); setProject(""); setExpires("90"); setAgent(defaultAgent); setApproval(defaultAgent); setDescription(""); } }, [open, defaultAgent]);
  const create = useMutation({
    mutationFn: () => api<ApiKeyCreated>("/api-keys", { method: "POST", body: { name, scopes: selected, project_ids: project ? [project] : null, expires_in_days: expires ? Number(expires) : null, kind: agent ? "agent" : "standard", requires_approval: agent || approval, description: description.trim() || null } }),
    onSuccess: (k) => onCreated(k),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const toggle = (s: string) => setSelected((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Create API key" description="Grant only the scopes this integration needs." className="max-w-xl">
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <FormField id="key-name" label="Name"><Input id="key-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={agent ? "Support agent" : "CI deploys"} maxLength={64} autoFocus /></FormField>
          <div className="space-y-3 rounded-lg border border-border p-3">
            <label className="flex items-start justify-between gap-4">
              <span><span className="flex items-center gap-1.5 text-[13px] font-medium"><Bot className="size-4 text-muted" aria-hidden /> This key is for an AI agent or automation</span>
                <span className="block text-xs text-muted">Agent keys get their own activity view, and deleting or rotating anything always waits for your approval.</span></span>
              <Switch checked={agent} onCheckedChange={(v) => { setAgent(v); if (v) setApproval(true); }} aria-label="Agent key" />
            </label>
            <label className="flex items-start justify-between gap-4">
              <span><span className="text-[13px] font-medium">Require my approval for destructive actions</span>
                <span className="block text-xs text-muted">Deleting databases, projects or backups and rotating passwords is held until you approve it here.</span></span>
              <Switch checked={agent || approval} disabled={agent} onCheckedChange={setApproval} aria-label="Require approval" />
            </label>
            {agent && <FormField id="key-desc" label="What does this agent do?" hint="Optional. Shown next to its activity and approval requests."><Input id="key-desc" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={300} placeholder="Creates preview databases for pull requests" /></FormField>}
          </div>
          <div>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <span className="text-[13px] font-medium">Scopes</span>
              <div className="flex gap-1">{Object.keys(PRESETS).map((p) => <Button key={p} type="button" size="sm" variant="ghost" onClick={() => setSelected(PRESETS[p]!)}>{p}</Button>)}</div>
            </div>
            <div className="grid max-h-56 gap-1.5 overflow-y-auto rounded-lg border border-border p-2 sm:grid-cols-2">
              {scopes.data?.map((s) => (
                <label key={s.scope} className="flex cursor-pointer gap-2 rounded-md p-1.5 text-[13px] hover:bg-surface-2">
                  <input type="checkbox" className="mt-0.5 size-4 accent-[var(--accent)]" checked={selected.includes(s.scope)} onChange={() => toggle(s.scope)} />
                  <span><span className="font-mono text-xs">{s.scope}</span><span className="block text-xs text-muted">{s.description}</span></span>
                </label>
              ))}
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField id="key-project" label="Restrict to project" hint="Recommended for automation.">
              <NativeSelect id="key-project" value={project} onChange={(e) => setProject(e.target.value)}>
                <option value="">All projects</option>
                {projects.data?.items.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </NativeSelect>
            </FormField>
            <FormField id="key-exp" label="Expires">
              <NativeSelect id="key-exp" value={expires} onChange={(e) => setExpires(e.target.value)}>
                <option value="30">In 30 days</option><option value="90">In 90 days</option><option value="365">In 1 year</option><option value="">Never</option>
              </NativeSelect>
            </FormField>
          </div>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!name.trim() || selected.length === 0} loading={create.isPending}>Create key</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function NewKeyDialog({ created, setCreated }: { created: ApiKeyCreated | null; setCreated: (k: ApiKeyCreated | null) => void }) {
  return (
    <Dialog open={!!created} onOpenChange={(o) => !o && setCreated(null)}>
      <DialogContent title="Your new API key" description="Copy it now — for your security we won't show it again.">
        <div className="flex gap-3 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2.5 text-[13px]">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" /> Store it in a secret manager. Anyone with this key can act with its scopes.
        </div>
        <div className="mt-4 flex items-center gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
          <code className="min-w-0 flex-1 break-all font-mono text-[13px]" data-testid="new-api-key">{created?.key}</code>
          {created && <CopyButton value={created.key} label="Copy API key" />}
        </div>
        <DialogFooter><Button onClick={() => setCreated(null)}>I&apos;ve saved it</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
