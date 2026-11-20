import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CopyButton } from "@/components/ui/copy-button";
import { Meter } from "@/components/ui/progress";
import { StatusBadge } from "@/components/ui/status-badge";

describe("StatusBadge", () => {
  it("labels states in words, not only color", () => {
    render(<StatusBadge status="ready" />);
    expect(screen.getByText("Running")).toBeInTheDocument();
  });
  it("falls back to the raw status", () => {
    render(<StatusBadge status="mystery" />);
    expect(screen.getByText("mystery")).toBeInTheDocument();
  });
});

describe("ConfirmDialog", () => {
  it("requires typing the confirmation text before confirming", async () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog open onOpenChange={() => {}} title="Delete db?" confirmText="orders" confirmLabel="Delete" onConfirm={onConfirm} />);
    const button = screen.getByRole("button", { name: "Delete" });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Type/), "order");
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Type/), "s");
    expect(button).toBeEnabled();
    await userEvent.click(button);
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});

describe("CopyButton", () => {
  it("copies the value and announces it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<CopyButton value="postgresql://x" label="Copy URL" />);
    await userEvent.click(screen.getByRole("button", { name: "Copy URL" }));
    expect(writeText).toHaveBeenCalledWith("postgresql://x");
    expect(await screen.findByText("Copied to clipboard")).toBeInTheDocument();
  });
});

describe("Meter", () => {
  it("exposes an accessible meter", () => {
    render(<Meter value={42.4} label="Storage" />);
    const m = screen.getByRole("meter", { name: "Storage" });
    expect(m).toHaveAttribute("aria-valuenow", "42");
  });
});
