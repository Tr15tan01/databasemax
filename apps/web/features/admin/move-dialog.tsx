"use client";
import { useQuery } from "@tanstack/react-query";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { NativeSelect, Textarea } from "@/components/ui/input";
import { api } from "@/lib/api";

interface HostOption { id: string; name: string; status: string; region: string; heartbeat_fresh: boolean; allocated: { instances: number }; capacity: { max_instances?: number } }

/** Operator-initiated move of a database to another host (same URL and credentials). */
export function MoveDialog({ open, onOpenChange, currentHost, region, loading, onConfirm }: {
  open: boolean; onOpenChange: (o: boolean) => void; currentHost: string | null; region: string; loading?: boolean;
  onConfirm: (targetHostId: string, reason: string) => void;
}) {
  const hosts = useQuery({ queryKey: ["admin", "hosts"], queryFn: () => api<{ items: HostOption[] }>("/admin/hosts"), enabled: open });
  const options = (hosts.data?.items ?? []).filter((h) => h.name !== currentHost && h.status === "active" && h.region === region && h.heartbeat_fresh);
  const [target, setTarget] = React.useState("");
  const [reason, setReason] = React.useState("");
  React.useEffect(() => { if (!open) { setTarget(""); setReason(""); } }, [open]);
  React.useEffect(() => { if (!target && options[0]) setTarget(options[0].id); }, [target, options]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Move to another host"
        description="Takes a consistent backup while the database is read-only, restores it on the target host and switches over. The connection URL and password don't change; expect a short outage.">
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); onConfirm(target, reason); }}>
          <FormField id="move-target" label="Target host" hint={options.length === 0 ? "No other active host with a fresh heartbeat in this region." : undefined}>
            <NativeSelect id="move-target" value={target} onChange={(e) => setTarget(e.target.value)} disabled={options.length === 0}>
              {options.map((h) => <option key={h.id} value={h.id}>{h.name} ({h.allocated.instances}/{h.capacity.max_instances ?? "?"} instances)</option>)}
            </NativeSelect>
          </FormField>
          <FormField id="move-reason" label="Reason" hint="Recorded in the audit log with your identity.">
            <Textarea id="move-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} minLength={3} maxLength={500} />
          </FormField>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!target || reason.trim().length < 3} loading={loading}>Move database</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
