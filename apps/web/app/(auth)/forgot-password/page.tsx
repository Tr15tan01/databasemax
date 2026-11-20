"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { MailCheck } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { AuthShell } from "@/features/auth/auth-shell";
import { api, errorMessage } from "@/lib/api";
import { emailSchema } from "@/lib/validation";

const schema = z.object({ email: emailSchema });

export default function ForgotPasswordPage() {
  const [sent, setSent] = React.useState(false);
  const form = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema), defaultValues: { email: "" } });
  const onSubmit = form.handleSubmit(async (v) => {
    try {
      await api("/auth/password/forgot", { method: "POST", body: v });
      setSent(true);
    } catch (e) {
      form.setError("root", { message: errorMessage(e) });
    }
  });
  return (
    <AuthShell
      title="Reset your password"
      description={sent ? undefined : "Enter your account email and we'll send a single-use reset link."}
      footer={<Link href="/login" className="font-medium text-foreground hover:underline">Back to sign in</Link>}
    >
      {sent ? (
        <div className="flex gap-3 text-[13px] text-muted" role="status">
          <MailCheck className="size-5 shrink-0 text-accent" />
          If an account exists for that email, a reset link is on its way. It expires in 30 minutes.
        </div>
      ) : (
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          {form.formState.errors.root && <p role="alert" className="text-[13px] text-critical">{form.formState.errors.root.message}</p>}
          <FormField id="email" label="Email" error={form.formState.errors.email?.message}>
            <Input id="email" type="email" autoComplete="email" autoFocus {...form.register("email")} />
          </FormField>
          <Button type="submit" className="w-full" loading={form.formState.isSubmitting}>Send reset link</Button>
        </form>
      )}
    </AuthShell>
  );
}
