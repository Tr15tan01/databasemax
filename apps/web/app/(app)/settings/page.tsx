"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut, Monitor, Trash2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { ThemeToggle } from "@/components/theme-toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useReauth } from "@/features/app/reauth-dialog";
import { api, errorMessage } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import type { Me, SessionInfo } from "@/lib/types";
import { formatDate, relativeTime } from "@/lib/utils";
import { passwordSchema } from "@/lib/validation";

const NOTIF_LABELS: Record<string, string> = {
  account_verified: "Account verified", database_ready: "Database ready", database_failed: "Provisioning failed", database_stopped: "Database stopped",
  database_started: "Database started", database_deleted: "Database deleted", credentials_rotated: "Credentials rotated", backup_completed: "Backup completed",
  backup_failed: "Backup failed", usage_threshold: "Usage thresholds", plan_changed: "Plan changes", payment_failed: "Payment failed",
  subscription_canceled: "Subscription canceled", security_login: "New sign-in", admin_action: "Actions by support",
};

function Profile({ me }: { me: Me }) {
  const qc = useQueryClient();
  const [name, setName] = React.useState(me.display_name ?? "");
  const save = useMutation({
    mutationFn: () => api<Me>("/account", { method: "PATCH", body: { display_name: name } }),
    onSuccess: (m) => { qc.setQueryData(["me"], m); toast.success("Profile saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><CardTitle>Profile</CardTitle></CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <FormField id="p-name" label="Display name"><Input id="p-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} /></FormField>
          <FormField id="p-email" label="Email" hint={me.email_verified ? "Verified" : "Not verified yet"}><Input id="p-email" value={me.email} readOnly disabled /></FormField>
        </CardContent>
        <CardFooter className="justify-end"><Button size="sm" onClick={() => save.mutate()} loading={save.isPending} disabled={name === (me.display_name ?? "")}>Save</Button></CardFooter>
      </Card>
      <Card>
        <CardHeader><div><CardTitle>Appearance</CardTitle><CardDescription>Choose light, dark, or follow your system.</CardDescription></div><ThemeToggle /></CardHeader>
      </Card>
      <p className="text-xs text-subtle">Member since {formatDate(me.created_at)} · Account ID <span className="font-mono">{me.id}</span></p>
    </div>
  );
}

function Security({ me }: { me: Me }) {
  const qc = useQueryClient();
  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [err, setErr] = React.useState<string | null>(null);
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api<SessionInfo[]>("/auth/sessions") });
  const change = useMutation({
    mutationFn: () => api("/auth/password/change", { method: "POST", body: { current_password: me.has_password ? current : null, new_password: next } }),
    onSuccess: () => { toast.success("Password updated", { description: "Other sessions were signed out." }); setCurrent(""); setNext(""); setErr(null); qc.invalidateQueries({ queryKey: ["sessions"] }); qc.invalidateQueries({ queryKey: ["me"] }); },
    onError: (e) => setErr(errorMessage(e)),
  });
  const revoke = useMutation({ mutationFn: (id: string) => api(`/auth/sessions/${id}`, { method: "DELETE" }), onSuccess: () => { toast.success("Session revoked"); qc.invalidateQueries({ queryKey: ["sessions"] }); } });
  const revokeOthers = useMutation({ mutationFn: () => api<{ message: string }>("/auth/sessions/revoke-others", { method: "POST" }), onSuccess: (r) => { toast.success(r.message); qc.invalidateQueries({ queryKey: ["sessions"] }); } });
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const r = passwordSchema.safeParse(next);
    if (!r.success) return setErr(r.error.issues[0]!.message);
    change.mutate();
  };
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><div><CardTitle>{me.has_password ? "Change password" : "Set a password"}</CardTitle><CardDescription>Changing your password signs out every other session.</CardDescription></div></CardHeader>
        <form onSubmit={submit}>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            {me.has_password && <FormField id="cur-pw" label="Current password"><Input id="cur-pw" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></FormField>}
            <FormField id="new-pw" label="New password" hint="At least 10 characters." error={err ?? undefined}><Input id="new-pw" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></FormField>
          </CardContent>
          <CardFooter className="justify-end"><Button size="sm" type="submit" loading={change.isPending} disabled={!next}>Update password</Button></CardFooter>
        </form>
      </Card>
      <Card>
        <CardHeader>
          <div><CardTitle>Active sessions</CardTitle><CardDescription>Devices currently signed in to your account.</CardDescription></div>
          <Button variant="secondary" size="sm" onClick={() => revokeOthers.mutate()} loading={revokeOthers.isPending}><LogOut /> Sign out others</Button>
        </CardHeader>
        <CardContent>
          {!sessions.data ? <Skeleton className="h-24" /> : (
            <ul className="divide-y divide-border">
              {sessions.data.map((s) => (
                <li key={s.id} className="flex items-center gap-3 py-3 text-[13px]">
                  <Monitor className="size-4 shrink-0 text-subtle" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate">{s.user_agent ?? "Unknown device"}</div>
                    <div className="text-xs text-subtle">{s.ip ?? "unknown IP"} · via {s.auth_method.replace("_", " ")} · active {relativeTime(s.last_seen_at)}</div>
                  </div>
                  {s.current ? <Badge variant="good">This device</Badge> : <Button variant="ghost" size="sm" onClick={() => revoke.mutate(s.id)}>Revoke</Button>}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><div><CardTitle>Sign-in methods</CardTitle><CardDescription>Password: {me.has_password ? "set" : "not set"} · Google: {me.linked_providers.includes("google") ? "linked" : "not linked"}</CardDescription></div></CardHeader>
      </Card>
    </div>
  );
}

function Notifications() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["notif-settings"], queryFn: () => api<{ type: string; essential: boolean; email: boolean }[]>("/account/notification-settings") });
  const save = useMutation({
    mutationFn: (v: { type: string; email: boolean }) => api("/account", { method: "PATCH", body: { preferences: { email_notifications: { [v.type]: v.email } } } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notif-settings"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader><div><CardTitle>Email notifications</CardTitle><CardDescription>All notifications also appear in the app. Security, failure and billing emails can&apos;t be turned off.</CardDescription></div></CardHeader>
      <CardContent>
        {!q.data ? <Skeleton className="h-60" /> : (
          <ul className="divide-y divide-border">
            {q.data.map((n) => (
              <li key={n.type} className="flex items-center justify-between gap-4 py-3 text-[13px]">
                <label htmlFor={`n-${n.type}`} className="flex items-center gap-2">{NOTIF_LABELS[n.type] ?? n.type}{n.essential && <Badge variant="outline">Required</Badge>}</label>
                <Switch id={`n-${n.type}`} checked={n.email} disabled={n.essential || save.isPending} onCheckedChange={(v) => save.mutate({ type: n.type, email: v })} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function Danger({ me }: { me: Me }) {
  const router = useRouter();
  const qc = useQueryClient();
  const { run, dialog } = useReauth();
  const [open, setOpen] = React.useState(false);
  const del = useMutation({
    mutationFn: () => run(() => api("/account", { method: "DELETE", body: { confirm: me.email } })),
    onSuccess: () => { qc.clear(); router.replace("/"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card className="border-critical/30">
      {dialog}
      <CardHeader>
        <div><CardTitle>Delete account</CardTitle><CardDescription>Deletes all your databases (including their data), projects and API keys, and cancels any subscription. This can&apos;t be undone.</CardDescription></div>
        <Button variant="danger-outline" size="sm" onClick={() => setOpen(true)}><Trash2 /> Delete account</Button>
      </CardHeader>
      <ConfirmDialog open={open} onOpenChange={setOpen} title="Delete your account?" description="Every database and all of its data will be permanently destroyed."
        confirmText={me.email} confirmLabel="Delete account" loading={del.isPending} onConfirm={() => del.mutate()} />
    </Card>
  );
}

function Inner() {
  const { data: me } = useMe();
  const params = useSearchParams();
  const router = useRouter();
  const tab = params.get("tab") ?? "profile";
  if (!me) return <Skeleton className="h-60" />;
  return (
    <>
      <PageHeader title="Settings" description="Manage your profile, security and notifications." />
      <Tabs value={tab} onValueChange={(v) => router.replace(`/settings?tab=${v}`, { scroll: false })}>
        <TabsList>
          <TabsTrigger value="profile">Profile</TabsTrigger>
          <TabsTrigger value="security">Security</TabsTrigger>
          <TabsTrigger value="notifications">Notifications</TabsTrigger>
          <TabsTrigger value="account">Account</TabsTrigger>
        </TabsList>
        <TabsContent value="profile"><Profile me={me} /></TabsContent>
        <TabsContent value="security"><Security me={me} /></TabsContent>
        <TabsContent value="notifications"><Notifications /></TabsContent>
        <TabsContent value="account"><Danger me={me} /></TabsContent>
      </Tabs>
    </>
  );
}

export default function SettingsPage() {
  return <React.Suspense><Inner /></React.Suspense>;
}
