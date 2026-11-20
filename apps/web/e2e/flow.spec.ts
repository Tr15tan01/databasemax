import { execFileSync } from "node:child_process";

import { expect, test } from "@playwright/test";

import { deleteDatabase, registerAndVerify } from "./helpers";

test("register, verify, onboard, provision and connect", async ({ page, request }) => {
  const { email, password } = await registerAndVerify(page, request);

  // Onboarding: welcome -> project -> database -> provisioning -> connection
  await expect(page.getByRole("heading", { name: /Welcome to DatabaseMax/ })).toBeVisible();
  await page.getByRole("button", { name: "Get started" }).click();
  await page.getByLabel("Project name").fill("E2E App");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Create your first PostgreSQL database" })).toBeVisible();
  await page.getByLabel("Database name").fill("orders");
  await page.getByRole("button", { name: "Create database" }).click();
  await expect(page.getByRole("heading", { name: "Provisioning your database" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /orders is ready/ })).toBeVisible({ timeout: 90_000 });

  // Reveal the password and read the URL.
  await page.getByRole("button", { name: "Reveal password" }).click();
  await expect(page.getByText("Password visible")).toBeVisible();
  const url = (await page.getByTestId("connection-url").textContent())!.trim();
  expect(url).toMatch(/^postgresql:\/\/u_[a-z0-9]+:[A-Za-z0-9]+@.+\?sslmode=require$/);

  // The URL actually works (psql through the TLS gateway).
  if (process.env.E2E_PSQL !== "0") {
    const target = process.env.E2E_GATEWAY_ADDR ? `${url}&hostaddr=${process.env.E2E_GATEWAY_ADDR}` : url;
    const out = execFileSync("psql", [target, "-tAc", "select current_database()"], { encoding: "utf8" }).trim();
    expect(out).toBe("orders");
  }

  // Dashboard shows the running database.
  await page.goto("/dashboard");
  await expect(page.getByText("1 running")).toBeVisible();
  await page.goto("/databases");
  await expect(page.getByRole("link", { name: "orders" })).toBeVisible();

  // Sign out, sign back in.
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);

  // Clean up.
  await page.goto("/databases");
  await page.getByRole("link", { name: "orders" }).click();
  await expect(page).toHaveURL(/\/databases\/[0-9a-f-]{36}/);
  await deleteDatabase(page, page.url().split("/databases/")[1]!.split("?")[0]!, "orders");
});

test("login errors are generic", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("nobody@example.com");
  await page.getByLabel("Password").fill("wrong-password-123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Invalid email or password.")).toBeVisible();
});

test("protected pages redirect to login", async ({ page }) => {
  await page.goto("/databases");
  await expect(page).toHaveURL(/\/login\?next=%2Fdatabases/);
});

test("marketing pages render", async ({ page }) => {
  for (const [path, heading] of [
    ["/", "PostgreSQL, ready when you are."],
    ["/pricing", "Simple plans. Enforced limits. No surprises."],
    ["/security", "How we protect your data — and what's still on the list."],
    ["/docs/api", "API reference"],
  ] as const) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
  }
});
