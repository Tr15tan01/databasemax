"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useForm } from "react-hook-form";
import type { z } from "zod";

import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { AuthShell } from "@/features/auth/auth-shell";
import { GoogleButton } from "@/features/auth/google-button";
import { api, ApiError } from "@/lib/api";
import { safeNext } from "@/lib/hooks";
import type { Me } from "@/lib/types";
import { loginSchema } from "@/lib/validation";

const OAUTH_ERRORS: Record<string, string> = {
  oauth_cancelled: "Google sign-in was cancelled.",
  oauth_state_invalid: "Your sign-in session expired. Please try again.",
  oauth_email_unverified: "Your Google account's email isn't verified.",
  account_suspended: "This account is suspended. Contact support.",
  provider_disabled: "Google sign-in isn't enabled.",
};

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const next = safeNext(params.get("next"));
  const oauthError = params.get("error");
  const form = useForm<z.infer<typeof loginSchema>>({ resolver: zodResolver(loginSchema), defaultValues: { email: "", password: "" } });
  const onSubmit = form.handleSubmit(async (values) => {
    try {
      const me = await api<Me>("/auth/login", { method: "POST", body: values });
      qc.setQueryData(["me"], me);
      router.replace(next);
    } catch (e) {
      form.setError("root", { message: e instanceof ApiError ? e.message : "Couldn't sign in. Try again." });
    }
  });
  const { errors, isSubmitting } = form.formState;
  return (
    <AuthShell
      title="Sign in to DatabaseMax"
      footer={
        <>
          New here?{" "}
          <Link href="/register" className="font-medium text-foreground hover:underline">
            Create an account
          </Link>
        </>
      }
    >
      <GoogleButton next={next} />
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {(errors.root || oauthError) && (
          <p role="alert" className="rounded-md border border-critical/25 bg-critical-soft px-3 py-2 text-[13px] text-critical">
            {errors.root?.message ?? OAUTH_ERRORS[oauthError!] ?? "Sign-in failed. Please try again."}
          </p>
        )}
        <FormField id="email" label="Email" error={errors.email?.message}>
          <Input id="email" type="email" autoComplete="email" autoFocus aria-invalid={!!errors.email} {...form.register("email")} />
        </FormField>
        <FormField id="password" label={<span className="flex w-full justify-between">Password</span>} error={errors.password?.message}>
          <Input id="password" type="password" autoComplete="current-password" aria-invalid={!!errors.password} {...form.register("password")} />
        </FormField>
        <div className="flex justify-end">
          <Link href="/forgot-password" className="text-[13px] text-muted hover:text-foreground">
            Forgot password?
          </Link>
        </div>
        <Button type="submit" className="w-full" loading={isSubmitting}>
          Sign in
        </Button>
      </form>
    </AuthShell>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
