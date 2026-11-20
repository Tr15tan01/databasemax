"use client";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { api, ApiError, errorMessage } from "@/lib/api";

/**
 * Wraps a sensitive action: if the API answers `reauthentication_required`, ask for the password,
 * confirm it, then retry the action once.
 */
export function useReauth() {
  const [state, setState] = React.useState<{ resolve: (ok: boolean) => void } | null>(null);
  const run = React.useCallback(async <T,>(fn: () => Promise<T>): Promise<T> => {
    try {
      return await fn();
    } catch (e) {
      if (e instanceof ApiError && e.code === "reauthentication_required") {
        const ok = await new Promise<boolean>((resolve) => setState({ resolve }));
        if (ok) return fn();
      }
      throw e;
    }
  }, []);
  const dialog = <ReauthDialog open={!!state} onDone={(ok) => { state?.resolve(ok); setState(null); }} />;
  return { run, dialog };
}

function ReauthDialog({ open, onDone }: { open: boolean; onDone: (ok: boolean) => void }) {
  const [pw, setPw] = React.useState("");
  const [err, setErr] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => { if (!open) { setPw(""); setErr(null); } }, [open]);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/auth/reauthenticate", { method: "POST", body: { password: pw } });
      onDone(true);
    } catch (e2) {
      setErr(errorMessage(e2));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onDone(false)}>
      <DialogContent title="Confirm it's you" description="For your security, enter your password to continue. You won't be asked again for 15 minutes.">
        <form onSubmit={submit}>
          <FormField id="reauth-pw" label="Password" error={err ?? undefined}>
            <Input id="reauth-pw" type="password" autoComplete="current-password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} />
          </FormField>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onDone(false)}>Cancel</Button>
            <Button type="submit" loading={busy} disabled={!pw}>Confirm</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
