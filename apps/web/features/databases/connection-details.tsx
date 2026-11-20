"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Eye, EyeOff, KeyRound, Lock, RefreshCcw } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CodeBlock } from "@/components/ui/code-block";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CopyButton } from "@/components/ui/copy-button";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useReauth } from "@/features/app/reauth-dialog";
import { api, errorMessage } from "@/lib/api";
import type { Connection, Database } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

function Field({ label, value, copy = true, mono = true, children }: { label: string; value: string; copy?: boolean; mono?: boolean; children?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border py-2.5 last:border-0">
      <span className="w-28 shrink-0 text-[13px] text-muted">{label}</span>
      <span className={`min-w-0 flex-1 truncate text-[13px] ${mono ? "font-mono" : ""}`}>{value}</span>
      <span className="flex shrink-0 items-center gap-1">
        {children}
        {copy && <CopyButton value={value} label={`Copy ${label.toLowerCase()}`} />}
      </span>
    </div>
  );
}

function examples(url: string, c: Connection) {
  return {
    psql: { lang: "bash" as const, code: `psql "${url}"` },
    env: { lang: "dotenv" as const, code: `DATABASE_URL="${url}"` },
    node: {
      lang: "javascript" as const,
      code: `import pg from "pg";

const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL, max: 10 });
const { rows } = await pool.query("select now()");`,
    },
    prisma: {
      lang: "bash" as const,
      code: `# schema.prisma → datasource db { provider = "postgresql" url = env("DATABASE_URL") }
DATABASE_URL="${url}"`,
    },
    python: {
      lang: "python" as const,
      code: `import os, psycopg

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    print(conn.execute("select version()").fetchone())`,
    },
    go: {
      lang: "go" as const,
      code: `pool, err := pgxpool.New(ctx, os.Getenv("DATABASE_URL"))`,
    },
    params: {
      lang: "text" as const,
      code: `host=${c.host}\nport=${c.port}\ndbname=${c.database}\nuser=${c.username}\npassword=<password>\nsslmode=${c.sslmode}`,
    },
  };
}

export function ConnectionDetails({ database }: { database: Database }) {
  const qc = useQueryClient();
  const { run, dialog } = useReauth();
  const [revealed, setRevealed] = React.useState<Connection | null>(null);
  const [confirmRotate, setConfirmRotate] = React.useState(false);
  const conn = useQuery({ queryKey: ["connection", database.id], queryFn: () => api<Connection>(`/databases/${database.id}/connection`), enabled: database.status === "ready" });

  const reveal = useMutation({
    mutationFn: () => run(() => api<Connection>(`/databases/${database.id}/connection`, { query: { reveal: "true" } })),
    onSuccess: (c) => setRevealed(c),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const rotate = useMutation({
    mutationFn: () => api(`/databases/${database.id}/credentials/rotate`, { method: "POST" }),
    onSuccess: () => {
      setConfirmRotate(false);
      setRevealed(null);
      toast.success("Rotating credentials", { description: "The old password stops working as soon as rotation completes." });
      qc.invalidateQueries({ queryKey: ["databases", database.id] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  // Refresh connection info when a rotation finishes.
  const rotating = database.provisioning?.job_kind === "rotate" && ["queued", "running"].includes(database.provisioning.job_status ?? "");
  const wasRotating = React.useRef(rotating);
  React.useEffect(() => {
    if (wasRotating.current && !rotating) {
      qc.invalidateQueries({ queryKey: ["connection", database.id] });
      toast.success("Credentials rotated", { description: "Reveal the new password to update your applications." });
    }
    wasRotating.current = rotating;
  }, [rotating, qc, database.id]);

  // Hide the password again after 2 minutes.
  React.useEffect(() => {
    if (!revealed) return;
    const t = setTimeout(() => setRevealed(null), 120_000);
    return () => clearTimeout(t);
  }, [revealed]);

  if (database.status !== "ready")
    return <p className="rounded-lg border border-dashed border-border p-6 text-center text-[13px] text-muted">Connection details are available while the database is running.</p>;
  if (conn.isError) return <ErrorState error={conn.error} onRetry={() => conn.refetch()} />;
  if (!conn.data) return <Skeleton className="h-64" />;
  const c = revealed ?? conn.data;
  const url = revealed?.url ?? c.url_masked;
  const exampleUrl = revealed?.url ?? c.url_masked.replace("••••••••", "<password>");
  const ex = examples(exampleUrl, c);

  return (
    <div className="space-y-4">
      {dialog}
      {revealed && (
        <div role="status" className="flex gap-3 rounded-lg border border-warning/30 bg-warning-soft px-4 py-3 text-[13px]">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
          <div>
            <p className="font-medium">Password visible</p>
            <p className="text-muted">Anyone who can see your screen can use it. It will be hidden again in 2 minutes. This reveal was recorded in your activity log.</p>
          </div>
        </div>
      )}
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Connection string</CardTitle>
            <CardDescription>Standard PostgreSQL URL. Keep it secret — it contains your password.</CardDescription>
          </div>
          <Button variant="secondary" size="sm" onClick={() => (revealed ? setRevealed(null) : reveal.mutate())} loading={reveal.isPending}>
            {revealed ? <><EyeOff /> Hide</> : <><Eye /> Reveal password</>}
          </Button>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 rounded-lg border border-border bg-surface-2/60 px-3 py-2.5">
            <code className="min-w-0 flex-1 break-all font-mono text-[13px]" data-testid="connection-url">{url}</code>
            {revealed ? <CopyButton value={revealed.url} label="Copy connection URL" /> : (
              <Button variant="ghost" size="sm" onClick={() => reveal.mutate()}>Copy URL</Button>
            )}
          </div>
          <div className="mt-5">
            <Field label="Host" value={c.host} />
            <Field label="Port" value={String(c.port)} />
            <Field label="Database" value={c.database} />
            <Field label="Username" value={c.username} />
            <Field label="Password" value={revealed?.password ?? "••••••••••••••••"} copy={!!revealed}>
              {!revealed && <Button variant="ghost" size="sm" onClick={() => reveal.mutate()}>Reveal</Button>}
            </Field>
            <Field label="SSL mode" value={c.sslmode} />
          </div>
          <p className="mt-4 flex gap-2 text-xs text-muted">
            <Lock className="mt-0.5 size-3.5 shrink-0" aria-hidden /> {c.ssl_note}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Connect from your code</CardTitle>
            <CardDescription>{revealed ? "Examples include your real URL." : "Reveal the password to fill it into these examples."}</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="psql">
            <TabsList>
              {[["psql", "psql"], ["env", ".env"], ["node", "Node.js"], ["prisma", "Prisma"], ["python", "Python"], ["go", "Go"], ["params", "Parameters"]].map(([k, l]) => (
                <TabsTrigger key={k} value={k!}>{l}</TabsTrigger>
              ))}
            </TabsList>
            {Object.entries(ex).map(([k, v]) => (
              <TabsContent key={k} value={k} className="pt-4">
                <CodeBlock code={v.code} lang={v.lang} wrap />
              </TabsContent>
            ))}
          </Tabs>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2"><KeyRound className="size-4" /> Credential rotation</CardTitle>
            <CardDescription>
              Current password created {formatDateTime(c.credential_created_at)}. Rotating issues a new password, revokes the old one immediately and closes open sessions.
            </CardDescription>
          </div>
          <Button variant="secondary" size="sm" onClick={() => setConfirmRotate(true)} loading={rotating} disabled={rotating}>
            <RefreshCcw /> {rotating ? "Rotating…" : "Rotate password"}
          </Button>
        </CardHeader>
      </Card>

      <ConfirmDialog
        open={confirmRotate}
        onOpenChange={setConfirmRotate}
        title="Rotate the database password?"
        description="Applications using the current password will be disconnected and won't be able to reconnect until you update them with the new connection URL."
        confirmLabel="Rotate password"
        loading={rotate.isPending}
        onConfirm={() => rotate.mutate()}
      />
    </div>
  );
}
