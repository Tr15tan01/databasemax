"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2 } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { useForm } from "react-hook-form";
import type { z } from "zod";

import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { AuthShell } from "@/features/auth/auth-shell";
import { useFragmentToken } from "@/features/auth/use-fragment-token";
import { api, errorMessage } from "@/lib/api";
import { resetSchema } from "@/lib/validation";

export default function ResetPasswordPage() {
  const token = useFragmentToken();
  const [done, setDone] = React.useState(false);
  const form = useForm<z.infer<typeof resetSchema>>({ resolver: zodResolver(resetSchema), defaultValues: { password: "", confirm: "" } });
  const onSubmit = form.handleSubmit(async (v) => {
    try {
      await api("/auth/password/reset", { method: "POST", body: { token, password: v.password } });
      setDone(true);
    } catch (e) {
      form.setError("root", { message: errorMessage(e) });
    }
  });
  const { errors, isSubmitting } = form.formState;
  return (
    <AuthShell title="Choose a new password" description={done ? undefined : "All other sessions will be signed out."}>
      {done ? (
        <div className="space-y-4" role="status">
          <p className="flex gap-2 text-[13px]"><CheckCircle2 className="size-5 text-good" /> Password updated.</p>
          <Button className="w-full" asChild><Link href="/login">Sign in</Link></Button>
        </div>
      ) : token === null ? (
        <p className="text-[13px] text-muted">This reset link is incomplete. <Link href="/forgot-password" className="text-accent">Request a new one</Link>.</p>
      ) : (
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          {errors.root && <p role="alert" className="rounded-md bg-critical-soft px-3 py-2 text-[13px] text-critical">{errors.root.message}</p>}
          <FormField id="password" label="New password" hint="At least 10 characters." error={errors.password?.message}>
            <Input id="password" type="password" autoComplete="new-password" {...form.register("password")} />
          </FormField>
          <FormField id="confirm" label="Confirm password" error={errors.confirm?.message}>
            <Input id="confirm" type="password" autoComplete="new-password" {...form.register("confirm")} />
          </FormField>
          <Button type="submit" className="w-full" loading={isSubmitting} disabled={!token}>Update password</Button>
        </form>
      )}
    </AuthShell>
  );
}
