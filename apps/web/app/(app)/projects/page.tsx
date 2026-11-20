"use client";
import { useQuery } from "@tanstack/react-query";
import { Archive, ArchiveRestore, FolderKanban, MoreHorizontal, Pencil, Plus, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input, NativeSelect } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";
import { CreateProjectForm } from "@/features/projects/create-project-form";
import { EditProjectDialog, useProjectActions } from "@/features/projects/project-dialogs";
import { api } from "@/lib/api";
import { ENV_LABELS } from "@/lib/labels";
import type { Page, Project } from "@/lib/types";
import { formatBytes, formatDate, formatMB } from "@/lib/utils";

function ProjectCard({ p }: { p: Project }) {
  const [editing, setEditing] = React.useState(false);
  const { archive, askDelete, deleteDialog } = useProjectActions(p);
  return (
    <Card className="group relative flex flex-col p-5 transition-colors hover:border-border-strong">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link href={`/projects/${p.id}`} className="block truncate font-semibold after:absolute after:inset-0">{p.name}</Link>
          <p className="mt-1 line-clamp-2 min-h-[2.5em] text-[13px] text-muted">{p.description || "No description"}</p>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger className="relative z-10 rounded-md p-1 text-subtle hover:bg-surface-2 hover:text-foreground" aria-label={`Actions for ${p.name}`}>
            <MoreHorizontal className="size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => setEditing(true)}><Pencil /> Edit</DropdownMenuItem>
            {p.status === "archived" ? (
              <DropdownMenuItem onSelect={() => archive.mutate(false)}><ArchiveRestore /> Restore</DropdownMenuItem>
            ) : (
              <DropdownMenuItem onSelect={() => archive.mutate(true)}><Archive /> Archive</DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem destructive onSelect={askDelete}><Trash2 /> Delete</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Badge variant={p.environment === "production" ? "accent" : "outline"}>{ENV_LABELS[p.environment]}</Badge>
        {p.status === "archived" && <StatusBadge status="archived" />}
      </div>
      <dl className="mt-5 grid grid-cols-3 gap-2 border-t border-border pt-4 text-[13px]">
        <div><dt className="text-xs text-subtle">Databases</dt><dd className="mt-0.5 font-medium tabular">{p.database_count}</dd></div>
        <div><dt className="text-xs text-subtle">Storage</dt><dd className="mt-0.5 font-medium tabular">{formatBytes(p.storage_used_bytes)}</dd></div>
        <div><dt className="text-xs text-subtle">Allocated</dt><dd className="mt-0.5 font-medium tabular">{formatMB(p.allocated_storage_mb)}</dd></div>
      </dl>
      <p className="mt-3 text-xs text-subtle">Created {formatDate(p.created_at)}</p>
      <EditProjectDialog project={p} open={editing} onOpenChange={setEditing} />
      {deleteDialog}
    </Card>
  );
}

function ProjectsInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [q, setQ] = React.useState("");
  const [status, setStatus] = React.useState("active");
  const [creating, setCreating] = React.useState(params.get("new") === "1");
  const list = useQuery({
    queryKey: ["projects", "list", q, status],
    queryFn: () => api<Page<Project>>("/projects", { query: { q, status, page_size: 100 } }),
  });
  return (
    <>
      <PageHeader title="Projects" description="Projects group databases by application and environment."
        actions={<Button onClick={() => setCreating(true)}><Plus /> New project</Button>} />
      <div className="mb-5 flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-subtle" aria-hidden />
          <Input aria-label="Search projects" placeholder="Search projects…" className="pl-8" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <NativeSelect aria-label="Status" className="sm:w-40" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="active">Active</option><option value="archived">Archived</option><option value="all">All</option>
        </NativeSelect>
      </div>
      {list.isError ? <ErrorState error={list.error} onRetry={() => list.refetch()} /> : !list.data ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-52" />)}</div>
      ) : list.data.items.length === 0 ? (
        <Card><EmptyState icon={FolderKanban} title={q ? "No matching projects" : "No projects yet"} description={q ? "Try a different search." : "Create a project to start adding databases."}
          action={!q && <Button onClick={() => setCreating(true)}><Plus /> New project</Button>} /></Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{list.data.items.map((p) => <ProjectCard key={p.id} p={p} />)}</div>
      )}
      <Dialog open={creating} onOpenChange={(o) => { setCreating(o); if (!o && params.get("new")) router.replace("/projects"); }}>
        <DialogContent title="New project" description="Projects are free to create within your plan's limit.">
          <CreateProjectForm onCreated={(p) => { setCreating(false); router.push(`/projects/${p.id}`); }} secondary={<Button type="button" variant="secondary" onClick={() => setCreating(false)}>Cancel</Button>} />
        </DialogContent>
      </Dialog>
    </>
  );
}

export default function ProjectsPage() {
  return <React.Suspense><ProjectsInner /></React.Suspense>;
}
