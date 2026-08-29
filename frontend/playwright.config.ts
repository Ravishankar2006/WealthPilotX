import { defineConfig, devices } from "@playwright/test";

/**
 * §20's end-to-end layer.
 *
 * The PRD says these run "against a staging environment". There is no staging
 * environment and, per §17.2, this project is deliberately not publicly deployed —
 * so they run against the Docker Compose stack, which is the same images CI builds
 * and the same stack a developer runs locally. That is the closest honest
 * equivalent, and it is stated here rather than left for someone to discover that
 * "staging" meant "localhost".
 *
 * These deliberately do NOT stub the network. Vitest already covers every component
 * against mocked responses; the whole point of this layer is to exercise the real
 * API, the real database and the real models end to end. That makes them slower and
 * more fragile than the unit tests, which is the trade being made — a suite that
 * mocked the backend would pass on a stack whose backend was broken, and that is the
 * exact failure the M6 `/fairness/report` bug slipped through.
 */

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:5183";

export default defineConfig({
  testDir: "./e2e",
  // The flow is sequential by nature — register, then profile, then risk — and the
  // expensive endpoints are rate-limited to 10/min per user, so parallel workers
  // racing through them would exhaust the budget and fail on 429s that say nothing
  // about the code.
  fullyParallel: false,
  workers: 1,
  // Risk assessment and portfolio generation both run models. §16.1 allows 5 and 8
  // seconds respectively; this leaves room for a cold container on top.
  timeout: 90_000,
  expect: { timeout: 15_000 },
  // No retries. A flaky end-to-end test that passes on the second attempt is a
  // defect being hidden, and this suite is small enough to fix rather than paper
  // over.
  retries: 0,
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
