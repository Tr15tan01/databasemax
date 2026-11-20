import { expect, test } from "@playwright/test";

import { registerAndVerify } from "./helpers";

test.setTimeout(120_000);

test("agent key: destructive call waits for approval, owner approves in the dashboard", async ({ page, request }) => {
  await registerAndVerify(page, request);
  const origin = new URL(page.url()).origin;
  const csrf = (await page.context().cookies()).find((c) => c.name === "dmx_csrf")?.value ?? "";
  const created = await page.request.post("/api/v1/projects", {
    data: { name: "Agent sandbox", environment: "development" },
    headers: { "X-CSRF-Token": csrf, Origin: origin },
  });
  expect(created.status()).toBe(201);
  const project = (await created.json()) as { id: string; name: string };

  // Create an agent key from the Agents page.
  await page.goto("/agents");
  await expect(page.getByRole("heading", { name: "No agents yet" })).toBeVisible();
  await page.getByRole("button", { name: "New agent key" }).first().click();
  await page.getByRole("textbox", { name: "Name" }).fill("preview-bot");
  await expect(page.getByRole("switch", { name: "Agent key" })).toBeChecked();
  await expect(page.getByRole("switch", { name: "Require approval" })).toBeDisabled();
  await page.getByRole("button", { name: "Full access" }).click();
  await page.getByRole("button", { name: "Create key" }).click();
  const key = (await page.getByTestId("new-api-key").textContent())!.trim();
  await page.getByRole("button", { name: "I've saved it" }).click();
  await expect(page.getByTestId("agent-card")).toContainText("preview-bot");

  // The agent asks to delete the project: nothing happens yet.
  const auth = { Authorization: `Bearer ${key}` };
  const del = await request.delete(`/api/v1/projects/${project.id}?confirm=${encodeURIComponent(project.name)}`, { headers: auth });
  expect(del.status()).toBe(202);
  const body = (await del.json()) as { status: string; approval: { id: string } };
  expect(body.status).toBe("pending_approval");
  expect((await request.get(`/api/v1/projects/${project.id}`, { headers: auth })).status()).toBe(200);

  // The owner sees it in Approvals (with a nav badge) and approves.
  await page.goto(`/approvals?id=${body.approval.id}`);
  await expect(page.getByRole("link", { name: /Approvals\s*1/ })).toBeVisible();
  const card = page.getByTestId("pending-approval");
  await expect(card).toContainText("Delete project");
  await expect(card).toContainText("preview-bot");
  await card.getByRole("button", { name: "Approve" }).click();
  await page.getByRole("button", { name: "Approve and run" }).click();
  await expect(page.getByText("Nothing to approve")).toBeVisible();
  await expect(page.getByRole("table")).toContainText("Approved · done");

  const polled = await request.get(`/api/v1/approvals/${body.approval.id}`, { headers: auth });
  expect(((await polled.json()) as { status: string }).status).toBe("executed");
  expect((await request.get(`/api/v1/projects/${project.id}`, { headers: auth })).status()).toBe(404);

  // The key's changes are attributed to it in the activity log.
  await page.goto("/activity");
  await page.getByLabel("Filter by API key").selectOption({ label: "🤖 preview-bot" });
  await expect(page.getByRole("table")).toContainText("Asked for approval");
});
