import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BackupTable } from "@/features/backups/backup-table";
import type { Backup } from "@/lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const base: Backup = {
  id: "b1", database_id: "d1", database_name: "orders", database_status: "ready", kind: "scheduled", status: "completed",
  size_bytes: 2048, table_count: 4, postgres_version: "16", created_at: "2026-09-27T10:00:00Z", started_at: null,
  completed_at: "2026-09-27T10:00:05Z", duration_ms: 5000, expires_at: "2026-10-04T10:00:05Z", verified_at: null,
  verification_status: null, error_message: null, restorable: true,
};

function renderTable(items: Backup[], onRestore = vi.fn()) {
  render(<TooltipPrimitive.Provider><BackupTable items={items} showDatabase onRestore={onRestore} /></TooltipPrimitive.Provider>);
  return onRestore;
}

describe("BackupTable", () => {
  it("shows status in words and restores only restorable backups", async () => {
    const onRestore = renderTable([
      base,
      { ...base, id: "b2", status: "failed", restorable: false, size_bytes: null, error_message: "boom" },
      { ...base, id: "b3", kind: "final", verification_status: "passed", status: "verified", database_status: "deleted" },
    ]);
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText("Final (before delete)")).toBeInTheDocument();
    expect(screen.getByText("deleted")).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: /Restore backup/ });
    expect(buttons[0]).toBeEnabled();
    expect(buttons[1]).toBeDisabled();
    await userEvent.click(buttons[0]!);
    expect(onRestore).toHaveBeenCalledWith(expect.objectContaining({ id: "b1" }));
  });

  it("formats sizes and durations", () => {
    renderTable([base]);
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
    expect(screen.getByText("5.0 s")).toBeInTheDocument();
  });
});
