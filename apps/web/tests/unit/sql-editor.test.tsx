import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { flattenPlan, lineCol, quoteIdent, selectedSql, toCsv } from "@/features/databases/sql/sql-utils";
import { SqlTab } from "@/features/databases/tabs/sql";
import type { Database } from "@/lib/types";

describe("sql-utils", () => {
  it("escapes CSV and neutralises spreadsheet formulas", () => {
    const csv = toCsv(["a", "b"], [["=HYPERLINK(\"x\")", 'say "hi", ok'], [null, "-1"]]);
    expect(csv).toBe(`a,b\r\n"'=HYPERLINK(""x"")","say ""hi"", ok"\r\n,'-1`);
  });

  it("maps a PostgreSQL error position to line and column", () => {
    expect(lineCol("select 1;\nselec 2", 11)).toEqual({ line: 2, col: 1, text: "selec 2" });
  });

  it("runs only the selection when there is one", () => {
    expect(selectedSql("select 1; select 2", 10, 18)).toBe("select 2");
    expect(selectedSql("select 1", 3, 3)).toBe("select 1");
  });

  it("quotes identifiers only when needed", () => {
    expect(quoteIdent("orders")).toBe("orders");
    expect(quoteIdent("user")).toBe('"user"');
    expect(quoteIdent('My "Table"')).toBe('"My ""Table"""');
  });

  it("flattens EXPLAIN ANALYZE output and finds where the time goes", () => {
    const plan = [
      {
        Plan: {
          "Node Type": "Hash Join",
          "Total Cost": 100,
          "Plan Rows": 10,
          "Actual Total Time": 50,
          "Actual Rows": 10,
          "Actual Loops": 1,
          Plans: [
            { "Node Type": "Seq Scan", "Relation Name": "orders", Schema: "public", "Total Cost": 80, "Plan Rows": 5, "Actual Total Time": 40, "Actual Rows": 5000, "Actual Loops": 1 },
            { "Node Type": "Index Scan", "Relation Name": "users", "Total Cost": 5, "Plan Rows": 1, "Actual Total Time": 2, "Actual Rows": 1, "Actual Loops": 1 },
          ],
        },
        "Planning Time": 0.5,
        "Execution Time": 51,
      },
    ];
    const { nodes, executionMs } = flattenPlan(plan);
    expect(executionMs).toBe(51);
    expect(nodes.map((n) => [n.nodeType, n.depth])).toEqual([["Hash Join", 0], ["Seq Scan", 1], ["Index Scan", 1]]);
    expect(nodes[1]!.share).toBeCloseTo(0.8);
    expect(nodes[1]!.relation).toBe("public.orders");
    expect(nodes[1]!.misestimate).toBe(1000);
    expect(nodes[0]!.share).toBeCloseTo(0.16);
  });
});

const database = { id: "db-1", name: "orders", status: "ready" } as unknown as Database;

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><SqlTab database={database} /></QueryClientProvider>);
}

function json(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const okResult = {
  mode: "write",
  statement_count: 1,
  results: [{ kind: "command", command: "DROP TABLE", columns: [], rows: [], row_count: 0, truncated: false }],
  error: null,
  notices: [],
  duration_ms: 12,
  timed_out: false,
  truncated: false,
  rolled_back_open_transaction: false,
  omitted_result_sets: 0,
  plan: null,
  limits: { timeout_seconds: 15, max_rows: 1000, max_bytes: 3145728 },
};

describe("SqlTab", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asks for confirmation (typing the database name) before a DROP", async () => {
    document.cookie = "dmx_csrf=test-token";
    const bodies: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (url.includes("/schema")) return json(200, { tables: [], truncated: false });
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        bodies.push(body);
        if (!body.confirm_destructive) {
          return json(409, { error: { code: "confirmation_required", message: "Confirm", details: { kinds: ["DROP"] } } });
        }
        return json(200, okResult);
      }),
    );
    const user = userEvent.setup();
    renderTab();
    await user.click(screen.getByRole("switch", { name: "Allow writes" }));
    await user.click(screen.getByLabelText("SQL"));
    await user.paste("drop table old_orders");
    await user.click(screen.getByRole("button", { name: /Run/ }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Run statements that change data?");
    const dialogRun = screen.getAllByRole("button", { name: "Run" }).at(-1)!;
    expect(dialogRun).toBeDisabled();
    await user.click(screen.getByLabelText(/Type/));
    await user.paste("orders");
    expect(dialogRun).toBeEnabled();
    await user.click(dialogRun);
    await waitFor(() => expect(screen.getAllByText("DROP TABLE").length).toBeGreaterThan(0));
    expect(bodies.map((b) => [b.mode, b.confirm_destructive])).toEqual([["write", false], ["write", true]]);
  }, 30_000);
});
