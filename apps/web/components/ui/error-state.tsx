"use client";
import { AlertTriangle, RotateCw } from "lucide-react";

import { ApiError } from "@/lib/api";

import { Button } from "./button";

export function ErrorState({ error, onRetry, title = "Couldn't load this" }: { error: unknown; onRetry?: () => void; title?: string }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  const requestId = error instanceof ApiError ? error.requestId : null;
  return (
    <div role="alert" className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border px-6 py-12 text-center">
      <AlertTriangle className="mb-3 size-5 text-critical" aria-hidden />
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="mt-1 max-w-md text-[13px] text-muted">{message}</p>
      {requestId && <p className="mt-2 font-mono text-[11px] text-subtle">Request ID: {requestId}</p>}
      {onRetry && (
        <Button variant="secondary" size="sm" className="mt-4" onClick={onRetry}>
          <RotateCw /> Try again
        </Button>
      )}
    </div>
  );
}
