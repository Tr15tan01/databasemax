import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against a running stack (web on :3000, API, worker, provisioner, gateway).
 * Deploy it on a test VM (infra/scripts/host-setup.sh, init-env.sh --dev, deploy.sh local), then `pnpm test:e2e`.
 */
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/warmup.ts",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : undefined,
      },
    },
  ],
});
