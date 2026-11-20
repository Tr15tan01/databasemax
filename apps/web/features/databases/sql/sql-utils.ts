/** Pure helpers for the SQL editor (kept free of React so they're easy to test). */

/** Cells that spreadsheet apps would evaluate as formulas are prefixed with a quote. */
function csvCell(v: string | null): string {
  if (v === null) return "";
  const safe = /^[=+\-@\t\r]/.test(v) ? `'${v}` : v;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export function toCsv(columns: string[], rows: (string | null)[][]): string {
  return [columns.map(csvCell).join(","), ...rows.map((r) => r.map(csvCell).join(","))].join("\r\n");
}

export function toJson(columns: string[], rows: (string | null)[][]): string {
  return JSON.stringify(rows.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i] ?? null]))), null, 2);
}

/** Line/column (1-based) of a 1-based character position reported by PostgreSQL. */
export function lineCol(sql: string, position: number): { line: number; col: number; text: string } {
  const before = sql.slice(0, Math.max(position - 1, 0));
  const lines = before.split("\n");
  const line = lines.length;
  const col = lines.at(-1)!.length + 1;
  return { line, col, text: sql.split("\n")[line - 1] ?? "" };
}

export interface PlanNode {
  depth: number;
  nodeType: string;
  relation?: string;
  index?: string;
  detail?: string;
  totalCost: number;
  planRows: number;
  /** Share of the whole query spent in this node itself (0..1): actual time with ANALYZE, else cost. */
  share: number;
  actualMs?: number;
  actualRows?: number;
  loops?: number;
  /** actual rows / estimated rows when ANALYZE ran and they differ by 10x or more. */
  misestimate?: number;
}

type RawPlan = Record<string, unknown> & { Plans?: RawPlan[] };

const num = (v: unknown) => (typeof v === "number" ? v : 0);

export function flattenPlan(explain: unknown): { nodes: PlanNode[]; planningMs?: number; executionMs?: number } {
  const top = Array.isArray(explain) ? (explain[0] as Record<string, unknown> | undefined) : undefined;
  const root = top?.Plan as RawPlan | undefined;
  if (!root) return { nodes: [] };
  const analyzed = typeof root["Actual Total Time"] === "number";
  const inclusive = (p: RawPlan) => (analyzed ? num(p["Actual Total Time"]) * Math.max(num(p["Actual Loops"]), 1) : num(p["Total Cost"]));
  const total = inclusive(root) || 1;
  const nodes: PlanNode[] = [];
  const walk = (p: RawPlan, depth: number) => {
    const children = p.Plans ?? [];
    const self = Math.max(inclusive(p) - children.reduce((s, c) => s + inclusive(c), 0), 0);
    const planRows = num(p["Plan Rows"]);
    const node: PlanNode = {
      depth,
      nodeType: String(p["Node Type"] ?? "?"),
      relation: p["Relation Name"] ? `${p["Schema"] ? `${String(p["Schema"])}.` : ""}${String(p["Relation Name"])}` : undefined,
      index: p["Index Name"] ? String(p["Index Name"]) : undefined,
      detail: [p["Join Type"], p["Strategy"], p["Index Cond"] ?? p["Filter"] ?? p["Hash Cond"] ?? p["Merge Cond"]]
        .filter(Boolean)
        .map(String)
        .join(" · ") || undefined,
      totalCost: num(p["Total Cost"]),
      planRows,
      share: Math.min(self / total, 1),
    };
    if (analyzed) {
      node.actualMs = num(p["Actual Total Time"]);
      node.actualRows = num(p["Actual Rows"]);
      node.loops = num(p["Actual Loops"]);
      const est = Math.max(planRows, 1);
      const act = Math.max(node.actualRows, 1);
      const ratio = act > est ? act / est : est / act;
      if (ratio >= 10) node.misestimate = Math.round(ratio);
    }
    nodes.push(node);
    children.forEach((c) => walk(c, depth + 1));
  };
  walk(root, 0);
  return {
    nodes,
    planningMs: typeof top?.["Planning Time"] === "number" ? (top["Planning Time"] as number) : undefined,
    executionMs: typeof top?.["Execution Time"] === "number" ? (top["Execution Time"] as number) : undefined,
  };
}

/** Text of the current selection, or the whole editor when nothing is selected. */
export function selectedSql(value: string, start: number, end: number): string {
  const sel = value.slice(Math.min(start, end), Math.max(start, end));
  return sel.trim() ? sel : value;
}

// --- history: this browser tab only (sessionStorage), never sent anywhere ----------------------

export interface HistoryEntry {
  sql: string;
  at: string;
  ok: boolean;
  mode: "read" | "write" | "explain";
}

const HISTORY_MAX = 30;
const historyKey = (databaseId: string) => `dmx:sql-history:${databaseId}`;

export function readHistory(databaseId: string): HistoryEntry[] {
  try {
    const raw = sessionStorage.getItem(historyKey(databaseId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as HistoryEntry[]).slice(0, HISTORY_MAX) : [];
  } catch {
    return [];
  }
}

export function addHistory(databaseId: string, entry: HistoryEntry): HistoryEntry[] {
  const next = [entry, ...readHistory(databaseId).filter((h) => h.sql !== entry.sql)].slice(0, HISTORY_MAX);
  try {
    sessionStorage.setItem(historyKey(databaseId), JSON.stringify(next));
  } catch {
    /* storage unavailable or full: history is a convenience only */
  }
  return next;
}

export function clearHistory(databaseId: string): void {
  try {
    sessionStorage.removeItem(historyKey(databaseId));
  } catch {
    /* ignore */
  }
}

export function formatDuration(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

// Reserved words that are common table/column names and must be quoted.
const RESERVED = new Set(
  "all analyse analyze and any array as asc check collate column constraint create current_date current_role current_time current_timestamp current_user default desc distinct do else end except false fetch for foreign from grant group having in initially intersect into lateral leading limit localtime localtimestamp not null offset on only or order placing primary references returning select session_user some symmetric table then to trailing true union unique user using variadic when where window with".split(
    " ",
  ),
);

export function quoteIdent(name: string): string {
  return /^[a-z_][a-z0-9_$]*$/.test(name) && !RESERVED.has(name) ? name : `"${name.replace(/"/g, '""')}"`;
}
