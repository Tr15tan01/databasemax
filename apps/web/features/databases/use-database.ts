"use client";
import { useQuery } from "@tanstack/react-query";

import { IN_FLIGHT } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import type { Database } from "@/lib/types";

export function useDatabase(id: string | null) {
  return useQuery({
    queryKey: ["databases", id],
    enabled: !!id,
    queryFn: () => api<Database>(`/databases/${id}`),
    refetchInterval: (q) => {
      const d = q.state.data as Database | undefined;
      if (!d) return false;
      const rotating = d.provisioning?.job_kind === "rotate" && ["queued", "running"].includes(d.provisioning.job_status ?? "");
      return IN_FLIGHT.has(d.status) || rotating ? 1500 : 30_000;
    },
  });
}
