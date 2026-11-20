"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Cpu, Info, MemoryStick } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { useForm } from "react-hook-form";
import type { z } from "zod";

import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import type { Database, Options, Page, Project } from "@/lib/types";
import { cn, formatMB } from "@/lib/utils";
import { databaseSchema, slugPgName } from "@/lib/validation";

type Values = z.infer<typeof databaseSchema>;

export function CreateDatabaseForm({ defaultProjectId, onCreated, secondary }: { defaultProjectId?: string; onCreated: (d: Database) => void; secondary?: React.ReactNode }) {
  const qc = useQueryClient();
  const idem = React.useRef(newIdempotencyKey("database"));
  const options = useQuery({ queryKey: ["db-options"], queryFn: () => api<Options>("/databases/options") });
  const projects = useQuery({ queryKey: ["projects", "active"], queryFn: () => api<Page<Project>>("/projects", { query: { status: "active", page_size: 100 } }) });
  const [dbNameTouched, setDbNameTouched] = React.useState(false);

  const form = useForm<Values>({
    resolver: zodResolver(databaseSchema),
    defaultValues: { project_id: defaultProjectId ?? "", name: "", database_name: "", postgres_version: "", compute_profile: "nano", storage_mb: 500, environment: "development" },
  });
  const o = options.data;
  const ps = projects.data?.items;

  React.useEffect(() => {
    if (!o) return;
    form.setValue("postgres_version", o.default_postgres_version);
    form.setValue("storage_mb", Math.max(256, Math.min(o.storage.max_per_database_mb ?? 0, o.storage.remaining_mb ?? 0, 1024)));
  }, [o, form]);
  React.useEffect(() => {
    if (!ps?.length) return;
    const pid = defaultProjectId && ps.some((p) => p.id === defaultProjectId) ? defaultProjectId : ps[0]!.id;
    form.setValue("project_id", pid);
    form.setValue("environment", ps.find((p) => p.id === pid)!.environment as Values["environment"]);
  }, [ps, defaultProjectId, form]);

  const name = form.watch("name");
  React.useEffect(() => {
    if (!dbNameTouched) form.setValue("database_name", name ? slugPgName(name) : "");
  }, [name, dbNameTouched, form]);

  const onSubmit = form.handleSubmit(async (v) => {
    try {
      const d = await api<Database>("/databases", {
        method: "POST",
        headers: { "Idempotency-Key": idem.current },
        body: { ...v, database_name: v.database_name || null },
      });
      qc.invalidateQueries({ queryKey: ["databases"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onCreated(d);
    } catch (e) {
      idem.current = newIdempotencyKey("database");
      if (e instanceof ApiError && e.code === "name_taken") form.setError("name", { message: e.message });
      else form.setError("root", { message: e instanceof Error ? e.message : "Couldn't create the database." });
    }
  });

  if (options.isLoading || projects.isLoading) return <div className="space-y-3"><Skeleton className="h-9" /><Skeleton className="h-9" /><Skeleton className="h-24" /></div>;
  if (ps && ps.length === 0)
    return (
      <p className="text-[13px] text-muted">
        You need an active project first. <Link href="/projects?new=1" className="text-accent hover:underline">Create a project</Link>.
      </p>
    );
  const { errors, isSubmitting } = form.formState;
  const profile = form.watch("compute_profile");
  const storageMax = o ? Math.min(o.storage.max_per_database_mb ?? 0, o.storage.remaining_mb ?? 0) : 0;

  return (
    <form onSubmit={onSubmit} className="space-y-5" noValidate>
      {errors.root && (
        <div role="alert" className="rounded-md border border-critical/25 bg-critical-soft px-3 py-2 text-[13px] text-critical">
          {errors.root.message}{" "}
          {/quota|plan/i.test(errors.root.message ?? "") && <Link href="/billing" className="font-medium underline">See plans</Link>}
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField id="db-project" label="Project" error={errors.project_id?.message}>
          <NativeSelect id="db-project" {...form.register("project_id", {
            onChange: (e) => {
              const p = ps?.find((x) => x.id === e.target.value);
              if (p) form.setValue("environment", p.environment as Values["environment"]);
            },
          })}>
            {ps?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </NativeSelect>
        </FormField>
        <FormField id="db-env" label="Environment label">
          <NativeSelect id="db-env" {...form.register("environment")}>
            <option value="development">Development</option>
            <option value="staging">Staging</option>
            <option value="production">Production</option>
          </NativeSelect>
        </FormField>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField id="db-name" label="Database name" error={errors.name?.message}>
          <Input id="db-name" placeholder="orders" aria-invalid={!!errors.name} autoFocus {...form.register("name")} />
        </FormField>
        <FormField id="db-pgname" label="PostgreSQL database" hint="Used in your connection URL." error={errors.database_name?.message}>
          <Input id="db-pgname" className="font-mono" aria-invalid={!!errors.database_name} {...form.register("database_name", { onChange: () => setDbNameTouched(true) })} />
        </FormField>
      </div>
      <FormField id="db-version" label="PostgreSQL version">
        <NativeSelect id="db-version" {...form.register("postgres_version")}>
          {o?.postgres_versions.map((v) => <option key={v} value={v}>PostgreSQL {v}</option>)}
        </NativeSelect>
      </FormField>
      {o && o.regions.length > 1 && (
        <FormField id="db-region" label="Region">
          <NativeSelect id="db-region">{o.regions.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}</NativeSelect>
        </FormField>
      )}
      <fieldset>
        <legend className="mb-2 text-[13px] font-medium">Size</legend>
        <div className="grid gap-2 sm:grid-cols-3" role="radiogroup">
          {o?.compute_profiles.map((p) => {
            const available = Boolean(p.available);
            const selected = profile === p.id;
            return (
              <label
                key={String(p.id)}
                className={cn(
                  "relative cursor-pointer rounded-lg border p-3 text-[13px] transition-colors",
                  selected ? "border-foreground ring-1 ring-foreground" : "border-border hover:border-border-strong",
                  !available && "cursor-not-allowed opacity-50",
                )}
              >
                <input type="radio" value={String(p.id)} disabled={!available} className="sr-only" {...form.register("compute_profile")} />
                <div className="font-medium">{String(p.label)}</div>
                <div className="mt-1.5 flex gap-3 text-xs text-muted">
                  <span className="flex items-center gap-1"><Cpu className="size-3" />{String(p.cpus)} vCPU</span>
                  <span className="flex items-center gap-1"><MemoryStick className="size-3" />{String(p.memory_mb)} MB</span>
                </div>
                {!available && <div className="mt-1.5 text-[11px] text-subtle">Higher plans</div>}
              </label>
            );
          })}
        </div>
      </fieldset>
      <FormField
        id="db-storage"
        label="Storage allocation"
        error={errors.storage_mb?.message}
        hint={o ? `Up to ${formatMB(storageMax)} available on your ${o.plan} plan. Shared compute; limits are enforced.` : undefined}
      >
        <div className="flex items-center gap-2">
          <Input id="db-storage" type="number" min={256} max={storageMax} step={64} className="max-w-40 tabular" {...form.register("storage_mb", { valueAsNumber: true })} />
          <span className="text-[13px] text-muted">MB</span>
        </div>
      </FormField>
      {o && (
        <p className="flex gap-2 rounded-md bg-surface-2 px-3 py-2 text-xs text-muted">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          Up to {o.max_connections_per_db} concurrent connections. TLS is required for all connections.
        </p>
      )}
      <div className="flex justify-end gap-2">
        {secondary}
        <Button type="submit" loading={isSubmitting}>Create database</Button>
      </div>
    </form>
  );
}
