"use client";
import { useQuery } from "@tanstack/react-query";
import { Archive, ArchiveRestore, Database as DbIcon, Pencil, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Stat } from "@/components/ui/stat";
import { StatusBadge } from "@/components/ui/status-badge";
import { DatabaseTable } from "@/features/databases/database-table";
import { EditProjectDialog, useProjectActions } from "@/features/projects/project-dialogs";
import { api } from "@/lib/api";
import { ENV_LABELS } from "@/lib/labels";
import type { Database, Project } from "@/lib/types";
import { formatBytes, formatDate, formatMB } from "@/lib/utils";

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [editing, setEditing] = React.useState(false);
  const project = useQuery({ queryKey: ["projects", id], queryFn: () => api<Project>(`/projects/${id}`) });
  const dbs = useQuery({ queryKey: ["databases", "project", id], queryFn: () => api<Database[]>(`/projects/${id}/databases`), refetchInterval: 10_000 });
  const { archive, askDelete, deleteDialog } = useProjectActions(project.data, () => router.push("/projects"));
  if (project.isError) return <ErrorState error={project.error} onRetry={() => project.refetch()} />;
  const p = project.data;
  if (!p) return <Skeleton className="h-40" />;
  const connections = dbs.data?.reduce((a, d) => a + (d.connections_current ?? 0), 0) ?? 0;
  return (
    <>
      <PageHeader
        eyebrow={<Link href="/projects" className="hover:text-foreground">Projects</Link>}
        title={<span className="flex items-center gap-3">{p.name} <Badge variant={p.environment === "production" ? "accent" : "outline"}>{ENV_LABELS[p.environment]}</Badge>{p.status === "archived" && <StatusBadge status="archived" />}</span>}
        description={p.description || undefined}
        actions={
          <>
            <Button variant="secondary" onClick={() => setEditing(true)}><Pencil /> Edit</Button>
            {p.status === "active" && <Button asChild><Link href={`/databases/new?project=${p.id}`}><Plus /> New database</Link></Button>}
          </>
        }
      />
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Databases" value={p.database_count} icon={DbIcon} />
        <Stat label="Storage used" value={formatBytes(p.storage_used_bytes)} sub={`of ${formatMB(p.allocated_storage_mb)} allocated`} />
        <Stat label="Active connections" value={connections} sub="at the last measurement" />
      </div>
      <Card className="mt-6">
        <CardHeader><CardTitle>Databases</CardTitle></CardHeader>
        <CardContent className="px-0 pb-2">
          {!dbs.data ? <div className="px-5"><Skeleton className="h-24" /></div> : dbs.data.length === 0 ? (
            <EmptyState icon={DbIcon} title="No databases in this project" description={p.status === "archived" ? "Restore the project to add databases." : "Create a PostgreSQL database to get a connection URL."}
              action={p.status === "active" && <Button asChild><Link href={`/databases/new?project=${p.id}`}><Plus /> New database</Link></Button>} />
          ) : <DatabaseTable items={dbs.data} showProject={false} />}
        </CardContent>
      </Card>
      <Card className="mt-6 border-critical/30">
        <CardHeader>
          <div>
            <CardTitle>Danger zone</CardTitle>
            <CardDescription>Archiving hides the project and prevents new databases; existing databases keep running. Deleting requires the project to be empty.</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {p.status === "archived" ? (
            <Button variant="secondary" onClick={() => archive.mutate(false)} loading={archive.isPending}><ArchiveRestore /> Restore project</Button>
          ) : (
            <Button variant="secondary" onClick={() => archive.mutate(true)} loading={archive.isPending}><Archive /> Archive project</Button>
          )}
          <Button variant="danger-outline" onClick={askDelete}><Trash2 /> Delete project</Button>
        </CardContent>
      </Card>
      <p className="mt-4 text-xs text-subtle">Created {formatDate(p.created_at)} · ID <span className="font-mono">{p.id}</span></p>
      <EditProjectDialog project={p} open={editing} onOpenChange={setEditing} />
      {deleteDialog}
    </>
  );
}
