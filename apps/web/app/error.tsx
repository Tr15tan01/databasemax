"use client";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div role="alert" className="flex min-h-[60dvh] flex-col items-center justify-center gap-4 px-5 text-center">
      <AlertTriangle className="size-6 text-critical" aria-hidden />
      <h1 className="text-xl font-semibold">Something went wrong</h1>
      <p className="max-w-md text-sm text-muted">An unexpected error occurred while rendering this page. Try again, and contact support if it keeps happening.</p>
      {error.digest && <p className="font-mono text-xs text-subtle">Reference: {error.digest}</p>}
      <Button onClick={reset}>Try again</Button>
    </div>
  );
}
