"use client";
import { AlertTriangle, ChevronDown, Gauge, History, Info, Play, ShieldCheck, Trash2 } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useReauth } from "@/features/app/reauth-dialog";
import { api, ApiError, errorMessage } from "@/lib/api";
import type { Database, SqlRun, SqlRunResult } from "@/lib/types";
import { cn, relativeTime } from "@/lib/utils";

import { PlanView } from "../sql/plan-view";
import { ResultsView } from "../sql/results";
import { SchemaPanel } from "../sql/schema-panel";
import { addHistory, clearHistory, formatDuration, type HistoryEntry, lineCol, readHistory, selectedSql } from "../sql/sql-utils";

const PLACEHOLDER = "SELECT now();  -- Ctrl+Enter to run. Select text to run only that part.";
const draftKey = (id: string) => `dmx:sql-draft:${id}`;

function ErrorPanel({ result, sql }: { result: SqlRunResult; sql: string }) {
  const e = result.error!;
  const loc = e.position ? lineCol(sql, e.position) : null;
  return (
    <div role="alert" className="space-y-2 p-4 text-[13px]">
      <div className="flex items-start gap-2 text-critical">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
        <div>
          <span className="font-medium">{e.message}</span>
          {e.sqlstate && <span className="ml-2 font-mono text-xs opacity-75">SQLSTATE {e.sqlstate}</span>}
        </div>
      </div>
      {loc && (
        <pre className="overflow-x-auto rounded-md bg-surface-2 px-3 py-2 font-mono text-xs">
          <span className="text-subtle">line {loc.line}: </span>
          {loc.text}
          {"\n"}
          <span className="text-critical">{" ".repeat(`line ${loc.line}: `.length + loc.col - 1)}^</span>
        </pre>
      )}
      {e.detail && <p className="text-muted">{e.detail}</p>}
      {e.hint && <p className="text-muted">Hint: {e.hint}</p>}
      {e.sqlstate === "25006" && result.mode === "read" && (
        <p className="text-muted">The editor is read-only. Turn on <span className="font-medium text-foreground">Allow writes</span> to change data.</p>
      )}
    </div>
  );
}

export function SqlTab({ database: d }: { database: Database }) {
  const [sql, setSql] = React.useState("");
  const [write, setWrite] = React.useState(false);
  const [running, setRunning] = React.useState(false);
  const [result, setResult] = React.useState<{ res: SqlRunResult; sql: string; explain: SqlRun["explain"] } | null>(null);
  const [failure, setFailure] = React.useState<string | null>(null);
  const [confirm, setConfirm] = React.useState<{ kinds: string[]; req: SqlRun } | null>(null);
  const [history, setHistory] = React.useState<HistoryEntry[]>([]);
  const [schemaKey, setSchemaKey] = React.useState(0);
  const editor = React.useRef<HTMLTextAreaElement>(null);
  const { run: withReauth, dialog } = useReauth();

  React.useEffect(() => {
    setHistory(readHistory(d.id));
    try {
      setSql(sessionStorage.getItem(draftKey(d.id)) ?? "");
    } catch {
      /* no storage */
    }
  }, [d.id]);
  React.useEffect(() => {
    try {
      sessionStorage.setItem(draftKey(d.id), sql);
    } catch {
      /* no storage */
    }
  }, [d.id, sql]);

  const execute = async (req: SqlRun) => {
    setRunning(true);
    setFailure(null);
    try {
      const res = await withReauth(() => api<SqlRunResult>(`/databases/${d.id}/sql`, { method: "POST", body: req }));
      setResult({ res, sql: req.sql, explain: req.explain });
      setHistory(addHistory(d.id, { sql: req.sql, at: new Date().toISOString(), ok: !res.error, mode: req.explain !== "none" ? "explain" : req.mode! }));
      if (req.mode === "write" && req.explain === "none") setSchemaKey((k) => k + 1);
    } catch (e) {
      if (e instanceof ApiError && e.code === "confirmation_required") {
        setConfirm({ kinds: ((e.details as { kinds?: string[] })?.kinds) ?? [], req });
      } else if (!(e instanceof ApiError && e.code === "reauthentication_required")) {
        setFailure(errorMessage(e));
      }
    } finally {
      setRunning(false);
    }
  };

  const currentSql = () => {
    const el = editor.current;
    return el ? selectedSql(sql, el.selectionStart, el.selectionEnd) : sql;
  };
  const runQuery = (explain: SqlRun["explain"] = "none") => {
    const text = currentSql();
    if (!text.trim() || running) return;
    void execute({ sql: text, mode: write ? "write" : "read", explain, confirm_destructive: false });
  };
  const insert = (text: string) => {
    const el = editor.current;
    if (!el) return setSql((s) => s + text);
    const { selectionStart: a, selectionEnd: b } = el;
    const next = sql.slice(0, a) + text + sql.slice(b);
    setSql(next);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(a + text.length, a + text.length);
    });
  };
  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      runQuery();
    } else if (e.key === "Tab" && !e.shiftKey) {
      e.preventDefault();
      insert("  ");
    }
  };

  if (d.status !== "ready") {
    return <EmptyState icon={Play} title="The SQL editor needs a running database" description="Start the database (Settings) or wait for provisioning to finish." />;
  }
  const severe = confirm?.kinds.some((k) => k.startsWith("DROP") || k.startsWith("TRUNCATE") || k.includes("without WHERE"));
  const res = result?.res;
  return (
    <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
      <Card className="order-2 h-64 overflow-hidden lg:order-1 lg:h-[620px]">
        <SchemaPanel databaseId={d.id} onInsert={insert} refreshKey={schemaKey} />
      </Card>
      <div className="order-1 min-w-0 space-y-4 lg:order-2">
        <Card className={cn("overflow-hidden", write && "border-warning/50")}>
          <textarea
            ref={editor}
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={PLACEHOLDER}
            aria-label="SQL"
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            rows={10}
            className="block min-h-[200px] w-full resize-y bg-transparent p-4 font-mono text-[13px] leading-relaxed outline-none placeholder:text-subtle"
          />
          <div className="flex flex-wrap items-center gap-2 border-t border-border bg-surface-2/60 px-3 py-2">
            <Button size="sm" onClick={() => runQuery()} loading={running} disabled={!sql.trim()}>
              <Play /> Run <kbd className="ml-1 hidden rounded border border-white/30 px-1 text-[10px] sm:inline">Ctrl ↵</kbd>
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="secondary" disabled={!sql.trim() || running}>
                  <Gauge /> Explain <ChevronDown className="size-3" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuItem onSelect={() => runQuery("plan")}>Show plan (doesn&apos;t run the query)</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => runQuery("analyze")}>Explain analyze (runs read-only, rolled back)</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="ghost" aria-label="Query history"><History /> History</Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-80">
                <DropdownMenuLabel>This tab only · never stored on our servers</DropdownMenuLabel>
                {history.length === 0 && <p className="px-2 py-1.5 text-xs text-subtle">No queries yet.</p>}
                {history.map((h, i) => (
                  <DropdownMenuItem key={i} onSelect={() => setSql(h.sql)} className="flex-col items-start gap-0.5">
                    <span className="w-full truncate font-mono text-xs">{h.sql.replace(/\s+/g, " ").slice(0, 80)}</span>
                    <span className={cn("text-[11px]", h.ok ? "text-subtle" : "text-critical")}>
                      {h.mode} · {relativeTime(h.at)}{h.ok ? "" : " · failed"}
                    </span>
                  </DropdownMenuItem>
                ))}
                {history.length > 0 && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onSelect={() => { clearHistory(d.id); setHistory([]); }}><Trash2 /> Clear history</DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
            <div className="ml-auto flex items-center gap-2">
              {write ? <AlertTriangle className="size-3.5 text-warning" aria-hidden /> : <ShieldCheck className="size-3.5 text-good" aria-hidden />}
              <Label htmlFor="sql-write" className="text-xs">{write ? "Writes allowed" : "Read-only"}</Label>
              <Switch id="sql-write" checked={write} onCheckedChange={setWrite} aria-label="Allow writes" />
            </div>
          </div>
        </Card>

        {failure && (
          <p role="alert" className="rounded-lg border border-critical/30 bg-critical-soft px-4 py-3 text-[13px] text-critical">{failure}</p>
        )}

        {res && (
          <Card className="overflow-hidden">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-4 py-2.5 text-xs text-muted">
              <span className={cn("font-medium", res.error ? "text-critical" : "text-good")}>{res.error ? "Failed" : "Success"}</span>
              <span>{formatDuration(res.duration_ms)}</span>
              <span>{res.statement_count} statement{res.statement_count === 1 ? "" : "s"}</span>
              <span>{result.explain !== "none" ? "explain" : res.mode === "read" ? "read-only, rolled back" : "read & write"}</span>
            </div>
            {res.rolled_back_open_transaction && (
              <p className="flex gap-2 border-b border-border bg-warning-soft px-4 py-2 text-xs text-warning">
                <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                A transaction was left open (BEGIN without COMMIT), so its changes were rolled back. Add COMMIT to keep them.
              </p>
            )}
            {res.omitted_result_sets > 0 && (
              <p className="border-b border-border px-4 py-2 text-xs text-muted">{res.omitted_result_sets} more result set(s) not shown.</p>
            )}
            {res.notices.length > 0 && (
              <ul className="space-y-0.5 border-b border-border px-4 py-2 font-mono text-xs text-muted">
                {res.notices.map((n, i) => <li key={i}>NOTICE: {n}</li>)}
              </ul>
            )}
            {res.error && <ErrorPanel result={res} sql={result.sql} />}
            {res.plan != null && <PlanView plan={res.plan} />}
            {res.results.length > 0 && <ResultsView results={res.results} maxRows={res.limits.max_rows} />}
          </Card>
        )}

        {!res && !failure && (
          <p className="text-xs text-subtle">
            Queries run as your database user through the same encrypted connection your apps use, with a 15-second limit and up to 1,000 rows
            returned. Read-only mode runs inside a transaction that is always rolled back.
          </p>
        )}
      </div>

      <ConfirmDialog
        open={!!confirm}
        onOpenChange={(o) => !o && setConfirm(null)}
        title="Run statements that change data?"
        description={
          <span>
            This will run: <span className="font-medium text-foreground">{confirm?.kinds.join(", ")}</span> on <span className="font-mono">{d.name}</span>.
          </span>
        }
        confirmLabel="Run"
        confirmText={severe ? d.name : undefined}
        loading={running}
        onConfirm={() => {
          const req = confirm!.req;
          setConfirm(null);
          void execute({ ...req, confirm_destructive: true });
        }}
      />
      {dialog}
    </div>
  );
}
