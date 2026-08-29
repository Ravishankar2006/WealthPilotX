import { expect, test } from "@playwright/test";

/**
 * §20's end-to-end flow: register → profile → risk assessment → recommendation →
 * dashboard, plus the §17.1 documents that flow asks the user to accept.
 *
 * This is the only test in the project that exercises the browser, the API, the
 * database and the models together. Everything else tests one side of a boundary
 * against a stub of the other, which is fast and misses exactly the class of bug M6
 * shipped: `/fairness/report` returned 500 while the service tests, the endpoint
 * tests and the frontend fixture test were all green, because no test ever put a
 * real response through a real page.
 *
 * Nothing here is stubbed for that reason. It is slower and more brittle than the
 * unit suites, and that is the point of it.
 */

const PASSWORD = "correct-horse-battery-staple";

function uniqueEmail(): string {
  // A fresh account per run. The journey ends at a generated portfolio, and reusing
  // an account would mean the second run started at "already has one" and skipped
  // the transitions this test exists to cover.
  //
  // `example.com`, not `example.invalid`: `.invalid` is a reserved TLD and the API's
  // email validation rejects it outright. The first run of this test failed on that,
  // which is FR-01's field-level 422 working exactly as specified — and is the kind
  // of thing only a test that talks to the real API can discover.
  return `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

test.describe("the full journey", () => {
  test("register, complete a profile, assess risk, generate a portfolio, see it on the dashboard", async ({
    page,
  }) => {
    const email = uniqueEmail();

    // --- Register (FR-01, §17.1) -------------------------------------------
    await page.goto("/register");

    // The consent checkbox gates submission, and the documents it names must be
    // real. Before M6 they were bare words.
    const terms = page.getByRole("link", { name: "Terms of Service" });
    await expect(terms).toHaveAttribute("href", "/terms");
    await expect(page.getByRole("link", { name: "Privacy Policy" })).toHaveAttribute(
      "href",
      "/privacy",
    );

    const submit = page.getByRole("button", { name: /create account|register|sign up/i });
    await expect(submit).toBeDisabled();

    await page.locator("#email").fill(email);
    await page.locator("#password").fill(PASSWORD);
    await page.getByRole("checkbox").check();
    await expect(submit).toBeEnabled();
    await submit.click();

    // Registration continues to onboarding, not the dashboard — the guard that
    // regressed once in M5 and is pinned by a unit test too.
    await expect(page).toHaveURL(/\/onboarding$/);

    // --- Financial profile (FR-02) -----------------------------------------
    await page.locator("#age").fill("34");
    await page.locator("#investment_horizon").fill("15");
    await page.locator("#income").fill("82000");
    await page.locator("#savings").fill("25000");
    await page.locator("#risk_appetite").selectOption("MODERATE");
    await page.locator("#investment_goal").selectOption("GROWTH");
    await page.locator("#experience").selectOption("BEGINNER");
    await page.locator("#financial_literacy").selectOption("MEDIUM");
    await page.getByRole("button", { name: /save|continue/i }).click();

    await expect(page).toHaveURL(/\/dashboard$/);

    // --- Risk assessment (FR-03) -------------------------------------------
    // Nothing expensive runs on mount, by design: both model calls share a
    // 10 req/min budget, so a page load must not spend it. The empty state and its
    // button are what that decision looks like from outside.
    await expect(page.getByText("No risk assessment yet")).toBeVisible();
    await page.getByRole("button", { name: "Run risk assessment" }).click();

    await expect(page.getByText(/Risk score/i).first()).toBeVisible();
    const category = page.getByText(/(Low|Medium|High) risk/i).first();
    await expect(category).toBeVisible();

    // --- Portfolio (FR-10, FR-11) ------------------------------------------
    await page.getByRole("button", { name: "Generate portfolio" }).click();

    await expect(page.getByText(/Expected return/i).first()).toBeVisible();
    await expect(page.getByText(/Expected risk/i).first()).toBeVisible();

    // --- FR-15: all seven elements, without navigating ----------------------
    await page.goto("/dashboard");
    for (const label of [
      /Risk score/i,
      /Risk profile/i,
      /Market outlook/i,
      /Recommended portfolio/i,
      /Expected return/i,
      /Expected risk/i,
      /What drove this classification/i,
    ]) {
      await expect(page.getByText(label).first()).toBeVisible();
    }

    // §17.1 on a view that shows a recommendation.
    await expect(page.getByText(/does not provide licensed financial/i).first()).toBeVisible();

    // --- FR-12 / §19: the backtest is visible, not CLI-only -----------------
    await page.goto("/portfolio");
    // Scoped to the section, not the page. The first version of this assertion
    // allowed `[role="note"]` anywhere as the "explained why not" branch — and the
    // inline §17.1 disclaimer on that page carries `role="note"`, so it matched
    // unconditionally and the assertion could never fail.
    const backtest = page.locator('section[aria-labelledby="backtest-heading"]');
    await expect(backtest.getByRole("heading", { name: "Historical simulation" })).toBeVisible();

    // Either the metrics rendered, or the section said why they could not — never a
    // silent empty panel. Both are correct outcomes depending on how much
    // out-of-sample history this stack happens to hold.
    const metrics = backtest.getByRole("row", { name: /Sharpe ratio/i });
    const reason = backtest.locator('[role="note"]');
    await expect(metrics.or(reason).first()).toBeVisible();

    // Whichever branch ran, §19's cost assumption must be stated when the numbers
    // are — reporting a return without the friction it assumed is the thing §19
    // singles out.
    if (await metrics.isVisible()) {
      await expect(backtest.getByText(/bps per side on turnover/i)).toBeVisible();
    }
  });

  test("the legal documents are readable without an account", async ({ page }) => {
    // §17.1 requires acceptance at registration. A document behind an auth guard
    // cannot be read by the person being asked to accept it, which would make the
    // checkbox ornamental.
    await page.goto("/terms");
    await expect(page.getByRole("heading", { name: "Terms of Service" })).toBeVisible();
    await expect(page.getByText(/does not execute trades/i).first()).toBeVisible();
    await expect(page.getByText(/has not been reviewed by a lawyer/i)).toBeVisible();

    await page.goto("/privacy");
    await expect(page.getByRole("heading", { name: "Privacy Policy" })).toBeVisible();
    await expect(page.getByText(/right to access|Export your financial profile/i).first()).toBeVisible();
    await expect(page.getByText(/at least 20 users/i)).toBeVisible();
  });

  test("a protected page redirects an anonymous visitor to sign in", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login$/);
  });
});
