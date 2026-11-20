"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { useForm } from "react-hook-form";
import type { z } from "zod";

import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import type { Project } from "@/lib/types";
import { projectSchema } from "@/lib/validation";

export function CreateProjectForm({ onCreated, submitLabel = "Create project", secondary }: { onCreated: (p: Project) => void; submitLabel?: string; secondary?: React.ReactNode }) {
  const qc = useQueryClient();
  const idem = React.useRef(newIdempotencyKey("project"));
  const form = useForm<z.infer<typeof projectSchema>>({
    resolver: zodResolver(projectSchema),
    defaultValues: { name: "", environment: "development", description: "" },
  });
  const onSubmit = form.handleSubmit(async (v) => {
    try {
      const p = await api<Project>("/projects", {
        method: "POST",
        body: { ...v, description: v.description || null },
        headers: { "Idempotency-Key": idem.current },
      });
      await qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onCreated(p);
    } catch (e) {
      idem.current = newIdempotencyKey("project");
      if (e instanceof ApiError && e.code === "name_taken") form.setError("name", { message: e.message });
      else form.setError("root", { message: e instanceof Error ? e.message : "Couldn't create the project." });
    }
  });
  const { errors, isSubmitting } = form.formState;
  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      {errors.root && <p role="alert" className="rounded-md border border-critical/25 bg-critical-soft px-3 py-2 text-[13px] text-critical">{errors.root.message}</p>}
      <FormField id="project-name" label="Project name" error={errors.name?.message} hint="For example the name of your app.">
        <Input id="project-name" autoFocus placeholder="my-saas-app" aria-invalid={!!errors.name} {...form.register("name")} />
      </FormField>
      <FormField id="project-env" label="Environment" hint="A label to tell production from development at a glance.">
        <NativeSelect id="project-env" {...form.register("environment")}>
          <option value="development">Development</option>
          <option value="staging">Staging</option>
          <option value="production">Production</option>
        </NativeSelect>
      </FormField>
      <FormField id="project-desc" label="Description" optional error={errors.description?.message}>
        <Textarea id="project-desc" rows={2} {...form.register("description")} />
      </FormField>
      <div className="flex justify-end gap-2 pt-2">
        {secondary}
        <Button type="submit" loading={isSubmitting}>{submitLabel}</Button>
      </div>
    </form>
  );
}
