"use client";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

import { GoogleIcon } from "./auth-shell";

export function GoogleButton({ next }: { next?: string }) {
  const { data } = useQuery({ queryKey: ["providers"], queryFn: () => api<{ google: boolean }>("/auth/providers"), staleTime: Infinity });
  if (!data?.google) return null;
  const href = `/api/v1/auth/google/start${next ? `?next=${encodeURIComponent(next)}` : ""}`;
  return (
    <>
      <Button variant="secondary" className="w-full" asChild>
        <a href={href}>
          <GoogleIcon /> Continue with Google
        </a>
      </Button>
      <div className="my-5 flex items-center gap-3 text-xs text-subtle">
        <span className="h-px flex-1 bg-border" /> or <span className="h-px flex-1 bg-border" />
      </div>
    </>
  );
}
