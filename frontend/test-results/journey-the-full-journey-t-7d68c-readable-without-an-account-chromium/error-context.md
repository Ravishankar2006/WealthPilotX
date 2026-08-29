# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: journey.spec.ts >> the full journey >> the legal documents are readable without an account
- Location: e2e/journey.spec.ts:118:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByRole('heading', { name: 'Terms of Service' })
Expected: visible
Timeout: 15000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 15000ms
  - waiting for getByRole('heading', { name: 'Terms of Service' })

```

```yaml
- text: "Blocked request. This host (\"web\") is not allowed. To allow this host, add \"web\" to `server.allowedHosts` in vite.config.js."
```

# Test source

```ts
  23  |   // the transitions this test exists to cover.
  24  |   return `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.invalid`;
  25  | }
  26  | 
  27  | test.describe("the full journey", () => {
  28  |   test("register, complete a profile, assess risk, generate a portfolio, see it on the dashboard", async ({
  29  |     page,
  30  |   }) => {
  31  |     const email = uniqueEmail();
  32  | 
  33  |     // --- Register (FR-01, §17.1) -------------------------------------------
  34  |     await page.goto("/register");
  35  | 
  36  |     // The consent checkbox gates submission, and the documents it names must be
  37  |     // real. Before M6 they were bare words.
  38  |     const terms = page.getByRole("link", { name: "Terms of Service" });
  39  |     await expect(terms).toHaveAttribute("href", "/terms");
  40  |     await expect(page.getByRole("link", { name: "Privacy Policy" })).toHaveAttribute(
  41  |       "href",
  42  |       "/privacy",
  43  |     );
  44  | 
  45  |     const submit = page.getByRole("button", { name: /create account|register|sign up/i });
  46  |     await expect(submit).toBeDisabled();
  47  | 
  48  |     await page.locator("#email").fill(email);
  49  |     await page.locator("#password").fill(PASSWORD);
  50  |     await page.getByRole("checkbox").check();
  51  |     await expect(submit).toBeEnabled();
  52  |     await submit.click();
  53  | 
  54  |     // Registration continues to onboarding, not the dashboard — the guard that
  55  |     // regressed once in M5 and is pinned by a unit test too.
  56  |     await expect(page).toHaveURL(/\/onboarding$/);
  57  | 
  58  |     // --- Financial profile (FR-02) -----------------------------------------
  59  |     await page.locator("#age").fill("34");
  60  |     await page.locator("#investment_horizon").fill("15");
  61  |     await page.locator("#income").fill("82000");
  62  |     await page.locator("#savings").fill("25000");
  63  |     await page.locator("#risk_appetite").selectOption("MODERATE");
  64  |     await page.locator("#investment_goal").selectOption("GROWTH");
  65  |     await page.locator("#experience").selectOption("BEGINNER");
  66  |     await page.locator("#financial_literacy").selectOption("MEDIUM");
  67  |     await page.getByRole("button", { name: /save|continue/i }).click();
  68  | 
  69  |     await expect(page).toHaveURL(/\/dashboard$/);
  70  | 
  71  |     // --- Risk assessment (FR-03) -------------------------------------------
  72  |     // Nothing expensive runs on mount, by design: both model calls share a
  73  |     // 10 req/min budget, so a page load must not spend it. The empty state and its
  74  |     // button are what that decision looks like from outside.
  75  |     await expect(page.getByText("No risk assessment yet")).toBeVisible();
  76  |     await page.getByRole("button", { name: "Run risk assessment" }).click();
  77  | 
  78  |     await expect(page.getByText(/Risk score/i).first()).toBeVisible();
  79  |     const category = page.getByText(/(Low|Medium|High) risk/i).first();
  80  |     await expect(category).toBeVisible();
  81  | 
  82  |     // --- Portfolio (FR-10, FR-11) ------------------------------------------
  83  |     await page.getByRole("button", { name: "Generate portfolio" }).click();
  84  | 
  85  |     await expect(page.getByText(/Expected return/i).first()).toBeVisible();
  86  |     await expect(page.getByText(/Expected risk/i).first()).toBeVisible();
  87  | 
  88  |     // --- FR-15: all seven elements, without navigating ----------------------
  89  |     await page.goto("/dashboard");
  90  |     for (const label of [
  91  |       /Risk score/i,
  92  |       /Risk profile/i,
  93  |       /Market outlook/i,
  94  |       /Recommended portfolio/i,
  95  |       /Expected return/i,
  96  |       /Expected risk/i,
  97  |       /What drove this classification/i,
  98  |     ]) {
  99  |       await expect(page.getByText(label).first()).toBeVisible();
  100 |     }
  101 | 
  102 |     // §17.1 on a view that shows a recommendation.
  103 |     await expect(page.getByText(/does not provide licensed financial/i).first()).toBeVisible();
  104 | 
  105 |     // --- FR-12 / §19: the backtest is visible, not CLI-only -----------------
  106 |     await page.goto("/portfolio");
  107 |     const backtest = page.getByRole("heading", { name: "Historical simulation" });
  108 |     await expect(backtest).toBeVisible();
  109 | 
  110 |     // Either the metrics rendered, or the page said why they could not — never a
  111 |     // silent empty panel. Both are correct outcomes depending on how much
  112 |     // out-of-sample history this stack happens to hold.
  113 |     const table = page.getByRole("row", { name: /Sharpe ratio/i });
  114 |     const note = page.locator('[role="note"]');
  115 |     await expect(table.or(note).first()).toBeVisible();
  116 |   });
  117 | 
  118 |   test("the legal documents are readable without an account", async ({ page }) => {
  119 |     // §17.1 requires acceptance at registration. A document behind an auth guard
  120 |     // cannot be read by the person being asked to accept it, which would make the
  121 |     // checkbox ornamental.
  122 |     await page.goto("/terms");
> 123 |     await expect(page.getByRole("heading", { name: "Terms of Service" })).toBeVisible();
      |                                                                           ^ Error: expect(locator).toBeVisible() failed
  124 |     await expect(page.getByText(/does not execute trades/i).first()).toBeVisible();
  125 |     await expect(page.getByText(/has not been reviewed by a lawyer/i)).toBeVisible();
  126 | 
  127 |     await page.goto("/privacy");
  128 |     await expect(page.getByRole("heading", { name: "Privacy Policy" })).toBeVisible();
  129 |     await expect(page.getByText(/right to access|Export your financial profile/i).first()).toBeVisible();
  130 |     await expect(page.getByText(/at least 20 users/i)).toBeVisible();
  131 |   });
  132 | 
  133 |   test("a protected page redirects an anonymous visitor to sign in", async ({ page }) => {
  134 |     await page.goto("/dashboard");
  135 |     await expect(page).toHaveURL(/\/login$/);
  136 |   });
  137 | });
  138 | 
```