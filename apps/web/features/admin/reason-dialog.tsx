"use client";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Textarea } from "@/components/ui/input";

/** Admin actions always carry a reason, which is stored in the audit log. */
export function ReasonDialog({ open, onOpenChange, title, description, confirmLabel, destructive, loading, onConfirm }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: string; description?: string; confirmLabel: string; destructive?: boolean; loading?: boolean; onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = React.useState("");
  React.useEffect(() => { if (!open) setReason(""); }, [open]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={title} description={description}>
        <form onSubmit={(e) => { e.preventDefault(); onConfirm(reason); }}>
          <FormField id="reason" label="Reason" hint="Recorded in the audit log with your identity.">
            <Textarea id="reason" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} minLength={3} maxLength={500} autoFocus />
          </FormField>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" variant={destructive ? "danger" : "primary"} disabled={reason.trim().length < 3} loading={loading}>{confirmLabel}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
