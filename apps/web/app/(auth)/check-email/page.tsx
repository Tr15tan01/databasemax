"use client";
import { MailCheck } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { AuthShell } from "@/features/auth/auth-shell";

function Inner() {
  const email = useSearchParams().get("email");
  return (
    <AuthShell title="Check your inbox" footer={<Link href="/login" className="font-medium text-foreground hover:underline">Back to sign in</Link>}>
      <div className="flex gap-3">
        <MailCheck className="mt-0.5 size-5 shrink-0 text-accent" aria-hidden />
        <div className="space-y-2 text-[13px] leading-relaxed text-muted">
          <p>
            If {email ? <strong className="text-foreground">{email}</strong> : "that address"} can be registered, we&apos;ve sent a
            verification link. It expires in 48 hours.
          </p>
          <p>Already have an account with this email? Sign in instead — we&apos;ve sent you a note about it.</p>
        </div>
      </div>
    </AuthShell>
  );
}

export default function CheckEmailPage() {
  return (
    <Suspense>
      <Inner />
    </Suspense>
  );
}
