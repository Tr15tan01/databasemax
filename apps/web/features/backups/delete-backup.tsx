"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useReauth } from "@/features/app/reauth-dialog";
import { api, errorMessage } from "@/lib/api";
import type { Backup } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

/** Confirmation + mutation for deleting a backup. Returns [requestDelete, dialog]. */
export function useDeleteBackup(): [(b: Backup) => void, React.ReactNode] {
  const qc = useQueryClient();
  const { run, dialog } = useReauth();
  const [target, setTarget] = React.useState<Backup | null>(null);
  const del = useMutation({
    mutationFn: (b: Backup) => run(() => api(`/backups/${b.id}`, { method: "DELETE" })),
    onSuccess: () => {
      toast.success("Backup deleted");
      setTarget(null);
      qc.invalidateQueries({ queryKey: ["backups"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const node = (
    <>
      {dialog}
      <ConfirmDialog open={!!target} onOpenChange={(o) => !o && setTarget(null)} title="Delete this backup?"
        description={target ? `The backup taken ${formatDateTime(target.completed_at ?? target.created_at)} will be permanently removed and can't be restored.` : undefined}
        confirmLabel="Delete backup" loading={del.isPending} onConfirm={() => target && del.mutate(target)} />
    </>
  );
  return [setTarget, node];
}
