import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CreateKeyDialog } from "@/features/api-keys/create-key-dialog";
import { APPROVAL_STATUS } from "@/lib/labels";

function renderDialog(defaultAgent: boolean) {
  const calls: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    const json = String(url).includes("/scopes") ? [{ scope: "projects:read", description: "Read" }]
      : String(url).includes("/projects") ? { items: [], total: 0, page: 1, page_size: 100 }
      : { id: "k", key: "dmx_live_x" };
    return new Response(JSON.stringify(json), { status: String(url).endsWith("/api-keys") && init?.method === "POST" ? 201 : 200, headers: { "Content-Type": "application/json" } });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><CreateKeyDialog open defaultAgent={defaultAgent} onOpenChange={() => {}} onCreated={() => {}} /></QueryClientProvider>);
  return calls;
}

// Radix Switch measures itself; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;

afterEach(() => vi.unstubAllGlobals());

describe("CreateKeyDialog", () => {
  it("agent keys always require approval", async () => {
    const calls = renderDialog(true);
    expect(screen.getByRole("switch", { name: "Agent key" })).toBeChecked();
    const approval = screen.getByRole("switch", { name: "Require approval" });
    expect(approval).toBeChecked();
    expect(approval).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "bot");
    await userEvent.click(screen.getByRole("button", { name: "Create key" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/api-keys"))).toBe(true));
    const post = calls.find((c) => c.url.endsWith("/api-keys"))!.body as Record<string, unknown>;
    expect(post).toMatchObject({ name: "bot", kind: "agent", requires_approval: true });
  });

  it("standard keys can opt in", async () => {
    renderDialog(false);
    const approval = screen.getByRole("switch", { name: "Require approval" });
    expect(approval).not.toBeChecked();
    expect(approval).toBeEnabled();
    await userEvent.click(approval);
    expect(approval).toBeChecked();
  });
});

describe("approval labels", () => {
  it("describe every status in words", () => {
    for (const s of ["pending", "executed", "approved", "failed", "rejected", "expired"]) expect(APPROVAL_STATUS[s]?.label).toBeTruthy();
  });
});
