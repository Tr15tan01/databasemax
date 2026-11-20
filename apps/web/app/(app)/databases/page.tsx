"use client";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Database as DbIcon, Plus, Search } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input, NativeSelect } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { DatabaseTable, type SortKey } from "@/features/databases/database-table";
import { api } from "@/lib/api";
import type { Database, Page, Project } from "@/lib/types";

function useDebounced<T>(v: T, ms = 250) {
  const [d, setD] = React.useState(v);
  React.useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

export default function DatabasesPage() {
  const [q, setQ] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [project, setProject] = React.useState("");
  const [env, setEnv] = React.useState("");
  const [sort, setSort] = React.useState<SortKey>("created_at");
  const [order, setOrder] = React.useState<"asc" | "desc">("desc");
  const [page, setPage] = React.useState(1);
  const dq = useDebounced(q);
  React.useEffect(() => setPage(1), [dq, status, project, env, sort, order]);
  const projects = useQuery({ queryKey: ["projects", "all-for-filter"], queryFn: () => api<Page<Project>>("/projects", { query: { page_size: 100, status: "all" } }) });
  const list = useQuery({
    queryKey: ["databases", "list", dq, status, project, env, sort, order, page],
    queryFn: () => api<Page<Database>>("/databases", { query: { q: dq, status, project_id: project, environment: env, sort, order, page, page_size: 20 } }),
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  });
  const onSort = (k: SortKey) => {
    if (k === sort) setOrder(order === "asc" ? "desc" : "asc");
    else { setSort(k); setOrder(k === "name" ? "asc" : "desc"); }
  };
  const filtered = dq || status || project || env;
  return (
    <>
      <PageHeader title="Databases" description="Every PostgreSQL database across your projects."
        actions={<Button asChild><Link href="/databases/new"><Plus /> New database</Link></Button>} />
      <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-[1fr_160px_180px_160px]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-subtle" aria-hidden />
          <Input aria-label="Search databases" placeholder="Search by name or project…" className="pl-8" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <NativeSelect aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option><option value="running">Running</option><option value="provisioning">Provisioning</option>
          <option value="stopped">Stopped</option><option value="failed">Failed</option><option value="deleting">Deleting</option>
        </NativeSelect>
        <NativeSelect aria-label="Filter by project" value={project} onChange={(e) => setProject(e.target.value)}>
          <option value="">All projects</option>
          {projects.data?.items.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </NativeSelect>
        <NativeSelect aria-label="Filter by environment" value={env} onChange={(e) => setEnv(e.target.value)}>
          <option value="">All environments</option><option value="development">Development</option><option value="staging">Staging</option><option value="production">Production</option>
        </NativeSelect>
      </div>
      <Card>
        {list.isError ? <div className="p-5"><ErrorState error={list.error} onRetry={() => list.refetch()} /></div> : !list.data ? (
          <div className="space-y-2 p-5">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-11" />)}</div>
        ) : list.data.items.length === 0 ? (
          <EmptyState icon={DbIcon} title={filtered ? "No databases match these filters" : "No databases yet"}
            description={filtered ? "Clear or change the filters to see more." : "Create a PostgreSQL database and get a connection URL in seconds."}
            action={!filtered && <Button asChild><Link href="/databases/new"><Plus /> New database</Link></Button>} />
        ) : (
          <DatabaseTable items={list.data.items} sort={sort} order={order} onSort={onSort} />
        )}
      </Card>
      {list.data && <Pagination page={page} pageSize={20} total={list.data.total} onPage={setPage} />}
    </>
  );
}
