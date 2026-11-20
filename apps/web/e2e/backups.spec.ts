import { execFileSync } from "node:child_process";

import { expect, type Page, test } from "@playwright/test";

import { deleteDatabase, registerAndVerify } from "./helpers";

test.setTimeout(300_000);

function psql(url: string, sql: string): string {
  const target = process.env.E2E_GATEWAY_ADDR ? `${url}&hostaddr=${process.env.E2E_GATEWAY_ADDR}` : url;
  return execFileSync("psql", [target, "-tAc", sql], { encoding: "utf8" }).trim();
}

async function revealUrl(page: Page): Promise<string> {
  await page.getByRole("tab", { name: "connection" }).click();
  await page.getByRole("button", { name: "Reveal password" }).click();
  await expect(page.getByText("Password visible")).toBeVisible();
  return (await page.getByTestId("connection-url").textContent())!.trim();
}

test("backup, delete with final backup, restore into a new database", async ({ page, request }) => {
  test.skip(process.env.E2E_PSQL === "0", "needs psql and the gateway");
  await registerAndVerify(page, request);
  await page.getByRole("button", { name: "Get started" }).click();
  await page.getByLabel("Project name").fill("Backup App");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Database name").fill("ledger");
  await page.getByRole("button", { name: "Create database" }).click();
  await expect(page.getByRole("heading", { name: /ledger is ready/ })).toBeVisible({ timeout: 90_000 });

  await page.goto("/databases");
  await page.getByRole("link", { name: "ledger" }).click();
  await expect(page).toHaveURL(/\/databases\/[0-9a-f-]{36}/);
  const url = await revealUrl(page);
  psql(url, "CREATE TABLE entries (id serial PRIMARY KEY, memo text); INSERT INTO entries (memo) SELECT 'row ' || g FROM generate_series(1, 250) g");

  // On-demand backup from the Backups tab.
  await page.getByRole("tab", { name: "backups" }).click();
  await expect(page.getByText("No backups yet")).toBeVisible();
  await page.getByRole("button", { name: "Back up now" }).click();
  await expect(page.getByText("Completed", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("On demand")).toBeVisible();
  await expect(page.getByRole("button", { name: "Back up now" })).toBeDisabled(); // free plan: 1 per day

  // Delete with a final backup (default on while running).
  await page.getByRole("tab", { name: "settings" }).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByRole("switch")).toBeChecked();
  await page.getByLabel(/Type .* to confirm/).fill("ledger");
  await page.getByRole("button", { name: "Delete database" }).click();
  await expect(page).toHaveURL(/\/databases$/);

  // The final backup shows up on the account-wide Backups page and can be restored.
  await page.goto("/backups");
  await expect(page.getByRole("heading", { name: "Backups", level: 1 })).toBeVisible();
  const finalRow = page.getByRole("row").filter({ hasText: "Final (before delete)" });
  await expect(finalRow).toBeVisible({ timeout: 90_000 });
  // The source finishes deleting right after its final backup completes.
  await expect(async () => {
    await page.reload();
    await expect(finalRow.getByText("deleted")).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 60_000 });
  await finalRow.getByRole("button", { name: /Restore/ }).click();
  await expect(page.getByRole("dialog")).toContainText("Restore to a new database");
  await page.getByLabel("New database name").fill("ledger-restored");
  await page.getByRole("dialog").getByRole("button", { name: "Restore" }).click();
  await expect(page).toHaveURL(/\/databases\/[0-9a-f-]{36}/);
  await expect(page.getByRole("heading", { name: /ledger-restored/ })).toBeVisible();
  await expect(page.getByText("Ready", { exact: true }).first()).toBeVisible({ timeout: 120_000 });

  const restoredUrl = await revealUrl(page);
  expect(restoredUrl).not.toBe(url);
  expect(psql(restoredUrl, "SELECT count(*) FROM entries")).toBe("250");
  expect(psql(restoredUrl, "SELECT current_database()")).toBe("ledger");

  await deleteDatabase(page, page.url().split("/databases/")[1]!.split("?")[0]!, "ledger-restored");
});
