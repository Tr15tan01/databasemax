import { expect, type APIRequestContext, type Page } from "@playwright/test";

/** Reads the verification link from the development mailbox (only mounted when DMX_ENV != production). */
export async function verificationLink(request: APIRequestContext, email: string): Promise<string> {
  for (let i = 0; i < 30; i++) {
    const res = await request.get(`/api/v1/dev/mailbox?to=${encodeURIComponent(email)}`);
    if (res.ok()) {
      const mails = (await res.json()) as { subject: string; text: string }[];
      const m = mails.find((x) => /verify/i.test(x.subject));
      const link = m?.text.match(/https?:\/\/\S+#token=[\w-]+/)?.[0];
      if (link) return link;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("verification email not delivered");
}

export async function registerAndVerify(page: Page, request: APIRequestContext) {
  const email = `e2e-${Date.now()}@example.com`;
  const password = "e2e-Strong-passphrase-2026";
  await page.goto("/register");
  await page.getByLabel("Name").fill("E2E Tester");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Check your inbox" })).toBeVisible();
  const link = await verificationLink(request, email);
  await page.goto(new URL(link).pathname + new URL(link).hash);
  await expect(page).toHaveURL(/\/onboarding/, { timeout: 20_000 });
  return { email, password };
}

/** Deletes a database through the API with the page's session (keeps the dev data plane tidy). */
export async function deleteDatabase(page: Page, id: string, name: string): Promise<void> {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((c) => c.name === "dmx_csrf")?.value ?? "";
  const res = await page.request.delete(`/api/v1/databases/${id}?confirm=${encodeURIComponent(name)}`, {
    headers: { "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin },
  });
  expect([202, 409]).toContain(res.status());
}
