"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import type { z } from "zod";

import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { AuthShell } from "@/features/auth/auth-shell";
import { GoogleButton } from "@/features/auth/google-button";
import { api, ApiError } from "@/lib/api";
import { registerSchema } from "@/lib/validation";

export default function RegisterPage() {
  const router = useRouter();
  const form = useForm<z.input<typeof registerSchema>>({
    resolver: zodResolver(registerSchema),
    defaultValues: { email: "", password: "", display_name: "", accept: false as unknown as true },
  });
  const onSubmit = form.handleSubmit(async ({ email, password, display_name }) => {
    try {
      await api("/auth/register", { method: "POST", body: { email, password, display_name: display_name || null } });
      router.push(`/check-email?email=${encodeURIComponent(email)}`);
    } catch (e) {
      if (e instanceof ApiError && e.code === "weak_password") form.setError("password", { message: e.message });
      else form.setError("root", { message: e instanceof ApiError ? e.message : "Couldn't create your account." });
    }
  });
  const { errors, isSubmitting } = form.formState;
  return (
    <AuthShell
      title="Create your account"
      description="Free plan included. No credit card required."
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-foreground hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <GoogleButton next="/onboarding" />
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {errors.root && (
          <p role="alert" className="rounded-md border border-critical/25 bg-critical-soft px-3 py-2 text-[13px] text-critical">
            {errors.root.message}
          </p>
        )}
        <FormField id="display_name" label="Name" optional error={errors.display_name?.message}>
          <Input id="display_name" autoComplete="name" {...form.register("display_name")} />
        </FormField>
        <FormField id="email" label="Email" error={errors.email?.message}>
          <Input id="email" type="email" autoComplete="email" aria-invalid={!!errors.email} {...form.register("email")} />
        </FormField>
        <FormField id="password" label="Password" hint="At least 10 characters." error={errors.password?.message}>
          <Input id="password" type="password" autoComplete="new-password" aria-invalid={!!errors.password} {...form.register("password")} />
        </FormField>
        <label className="flex items-start gap-2.5 text-[13px] text-muted">
          <input type="checkbox" className="mt-0.5 size-4 accent-[var(--accent)]" {...form.register("accept")} />
          <span>
            I agree to the{" "}
            <Link href="/terms" className="text-foreground underline underline-offset-2">Terms</Link> and{" "}
            <Link href="/privacy" className="text-foreground underline underline-offset-2">Privacy policy</Link>.
          </span>
        </label>
        {errors.accept && <p role="alert" className="-mt-2 text-[13px] text-critical">{errors.accept.message}</p>}
        <Button type="submit" className="w-full" loading={isSubmitting}>
          Create account
        </Button>
      </form>
    </AuthShell>
  );
}
