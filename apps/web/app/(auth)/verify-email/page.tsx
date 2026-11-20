"use client";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { AuthShell } from "@/features/auth/auth-shell";
import { useFragmentToken } from "@/features/auth/use-fragment-token";
import { api, errorMessage } from "@/lib/api";
import type { Me } from "@/lib/types";

export default function VerifyEmailPage() {
  const token = useFragmentToken();
  const router = useRouter();
  const qc = useQueryClient();
  const [state, setState] = React.useState<"working" | "done" | "error">("working");
  const [message, setMessage] = React.useState("");
  const ran = React.useRef(false);
  React.useEffect(() => {
    if (token === undefined || ran.current) return;
    ran.current = true;
    if (!token) {
      setState("error");
      setMessage("This link is missing its token. Open the link from your email again.");
      return;
    }
    api<Me>("/auth/verify-email", { method: "POST", body: { token } })
      .then((me) => {
        qc.setQueryData(["me"], me);
        setState("done");
        setTimeout(() => router.replace("/onboarding"), 1200);
      })
      .catch((e) => {
        setState("error");
        setMessage(errorMessage(e));
      });
  }, [token, router, qc]);
  return (
    <AuthShell title="Verifying your email">
      <div aria-live="polite" className="flex items-start gap-3 text-[13px]">
        {state === "working" && (<><Loader2 className="size-5 animate-spin text-accent" /> <span className="text-muted">One moment…</span></>)}
        {state === "done" && (<><CheckCircle2 className="size-5 text-good" /> <span>Email verified. Taking you to setup…</span></>)}
        {state === "error" && (
          <div className="w-full space-y-4">
            <div className="flex gap-3"><XCircle className="size-5 shrink-0 text-critical" /> <span className="text-muted">{message}</span></div>
            <Button variant="secondary" className="w-full" asChild><Link href="/login">Sign in to request a new link</Link></Button>
          </div>
        )}
      </div>
    </AuthShell>
  );
}
