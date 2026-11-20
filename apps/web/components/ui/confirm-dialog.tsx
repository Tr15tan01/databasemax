"use client";
import { AlertTriangle } from "lucide-react";
import * as React from "react";

import { Button } from "./button";
import { Dialog, DialogContent, DialogFooter } from "./dialog";
import { Input } from "./input";
import { Label } from "./label";

/**
 * Confirmation for destructive actions. When `confirmText` is set, the user must type it exactly.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  confirmText,
  destructive = true,
  loading,
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  confirmText?: string;
  destructive?: boolean;
  loading?: boolean;
  onConfirm: () => void;
  children?: React.ReactNode;
}) {
  const [typed, setTyped] = React.useState("");
  React.useEffect(() => {
    if (!open) setTyped("");
  }, [open]);
  const ok = !confirmText || typed === confirmText;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={title} description={description}>
        {destructive && (
          <div className="mb-4 flex gap-3 rounded-lg border border-critical/25 bg-critical-soft px-3 py-2.5 text-[13px] text-critical">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span>This action can&apos;t be undone.</span>
          </div>
        )}
        {children}
        {confirmText && (
          <div className="space-y-1.5">
            <Label htmlFor="confirm-text">
              Type <span className="font-mono font-semibold">{confirmText}</span> to confirm
            </Label>
            <Input id="confirm-text" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" autoFocus />
          </div>
        )}
        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant={destructive ? "danger" : "primary"} disabled={!ok} loading={loading} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
