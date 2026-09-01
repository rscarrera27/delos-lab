import { defineConfig, devices } from "@playwright/test";

const external = process.env.DELOS_E2E_EXTERNAL === "1";
const baseURL = process.env.DELOS_E2E_BASE_URL ?? "http://127.0.0.1:9400";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: "line",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: external
    ? undefined
    : {
        command:
          'delos_e2e_runtime=$(mktemp -d /tmp/delos-lab-e2e.XXXXXX) && exec ../.venv/bin/delos-lab --runtime-dir "$delos_e2e_runtime" --port 9400',
        url: `${baseURL}/api/health`,
        timeout: 30_000,
        reuseExistingServer: false,
      },
});
