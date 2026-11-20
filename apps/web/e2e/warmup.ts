/**
 * Global setup: request the app's routes once before the tests so a `next dev` server compiles
 * them up front (cold compilation can otherwise exceed per-assertion timeouts). Harmless against
 * a production build.
 */
const ROUTES = [
  "/", "/login", "/register", "/check-email", "/verify-email", "/onboarding", "/dashboard", "/databases",
  "/databases/new", "/databases/00000000-0000-0000-0000-000000000000", "/backups", "/pricing", "/security",
  "/docs/api", "/features",
];

async function resetDevRateLimits(base: string): Promise<void> {
  // Development stacks only (the endpoint doesn't exist in production): many E2E sign-ups
  // from 127.0.0.1 would otherwise hit the per-IP registration limit.
  try {
    const r = await fetch(`${base}/api/v1/auth/csrf`);
    const token = ((await r.json()) as { csrf_token: string }).csrf_token;
    const cookie = r.headers.getSetCookie().map((c) => c.split(";")[0]).join("; ");
    await fetch(`${base}/api/v1/dev/reset-rate-limits`, {
      method: "POST",
      headers: { Cookie: cookie, "X-CSRF-Token": token, Origin: base },
    });
  } catch {
    // not a dev stack
  }
}

export default async function warmup(): Promise<void> {
  const base = process.env.E2E_BASE_URL ?? "http://localhost:3000";
  await resetDevRateLimits(base);
  for (const r of ROUTES) {
    try {
      await fetch(base + r, { redirect: "manual", signal: AbortSignal.timeout(180_000) });
    } catch {
      // the tests will report real problems
    }
  }
}
