import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import Dashboard from "../Dashboard";
import { AuthProvider } from "../../context/AuthContext";
import { ASSESSMENT, COMPLETE, mockApi, PORTFOLIO, PREDICTION, PROFILE } from "../../test/api-mock";

// Plotly is loaded through a dynamic import that jsdom cannot execute usefully.
// The chart's own accessibility is covered by its description prop; here the point
// is the surrounding page.
vi.mock("../../components/charts/AllocationChart", () => ({
  AllocationChart: () => <div data-testid="allocation-chart" />,
}));

function renderDashboard() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider>
        <Dashboard />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const FULL = {
  "/user/profile/completeness": { body: COMPLETE },
  "/user/profile": { body: PROFILE },
  "/risk/latest": { body: ASSESSMENT },
  "/portfolio/current": { body: PORTFOLIO },
  "/prediction": { body: PREDICTION },
};

describe("Dashboard — FR-15", () => {
  it("renders all seven required elements without navigation", async () => {
    mockApi(FULL);
    renderDashboard();

    // 1 risk score, 2 risk profile
    expect(await screen.findByText("0.476")).toBeInTheDocument();
    expect(screen.getAllByText(/medium risk/i).length).toBeGreaterThan(0);

    // 5 expected return, 6 expected risk
    expect(screen.getByText("19.6%")).toBeInTheDocument();
    expect(screen.getByText("8.4%")).toBeInTheDocument();

    // 4 recommended portfolio. SPY appears in the outlook strip too, so this
    // scopes to the portfolio panel rather than matching either.
    const portfolioPanel = screen.getByRole("region", { name: /recommended portfolio/i });
    expect(portfolioPanel).toHaveTextContent("SPY");
    expect(portfolioPanel).toHaveTextContent("AGG");

    // 7 recommendation explanations — the reason travels with the holding
    expect(screen.getByText(/among the highest in the candidate set/i)).toBeInTheDocument();

    // 3 market outlook
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /market outlook/i })).toBeInTheDocument(),
    );
  });

  it("carries the §17.1 disclaimer", async () => {
    mockApi(FULL);
    renderDashboard();
    expect(await screen.findByRole("note")).toHaveTextContent(/do not guarantee future results/i);
  });
});

describe("Dashboard — empty states", () => {
  it("asks for the profile first when it is incomplete", async () => {
    mockApi({
      "/user/profile/completeness": { body: { complete: false, missing_fields: ["age", "income"] } },
      "/user/profile": { status: 404, body: { error: { code: "profile_not_found", message: "none", fields: null } } },
      "/risk/latest": { status: 404, body: { error: { code: "no_risk_assessment", message: "none", fields: null } } },
      "/portfolio/current": { status: 404, body: { error: { code: "no_portfolio", message: "none", fields: null } } },
      "/prediction": { status: 404, body: { error: { code: "no_prediction", message: "none", fields: null } } },
    });
    renderDashboard();

    expect(await screen.findByText(/finish your financial profile/i)).toBeInTheDocument();
    expect(screen.getByText(/age, income/i)).toBeInTheDocument();
  });

  it("treats a 404 from risk and portfolio as an empty state, not an error", async () => {
    // The ordinary condition for a new account. Rendering it as a failure would
    // make every user's first visit look broken.
    mockApi({
      "/user/profile/completeness": { body: COMPLETE },
      "/user/profile": { body: PROFILE },
      "/risk/latest": { status: 404, body: { error: { code: "no_risk_assessment", message: "none", fields: null } } },
      "/portfolio/current": { status: 404, body: { error: { code: "no_portfolio", message: "none", fields: null } } },
      "/prediction": { status: 404, body: { error: { code: "no_prediction", message: "none", fields: null } } },
    });
    renderDashboard();

    expect(await screen.findByText(/no risk assessment yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("blocks portfolio generation until a risk assessment exists", async () => {
    mockApi({
      "/user/profile/completeness": { body: COMPLETE },
      "/user/profile": { body: PROFILE },
      "/risk/latest": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
      "/portfolio/current": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
      "/prediction": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
    });
    renderDashboard();

    expect(await screen.findByText(/run the risk assessment above first/i)).toBeInTheDocument();
  });
});

describe("Dashboard — nothing expensive runs on mount", () => {
  it("does not call the model endpoints until the user clicks", async () => {
    // Phase 5 plan, decision 2. Both endpoints run models and share a 10 req/min
    // budget; auto-running them would let a page refresh spend it silently and
    // generate a portfolio the user never asked for.
    mockApi({
      "/user/profile/completeness": { body: COMPLETE },
      "/user/profile": { body: PROFILE },
      "/risk/latest": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
      "/portfolio/current": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
      "/prediction": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
    });
    renderDashboard();
    await screen.findByText(/no risk assessment yet/i);

    const calls = vi.mocked(fetch).mock.calls.map(([input]) => String(input));
    expect(calls.some((url) => url.includes("/risk/analyze"))).toBe(false);
    expect(calls.some((url) => url.includes("/portfolio/generate"))).toBe(false);
  });

  it("runs the risk assessment when asked", async () => {
    const user = userEvent.setup();
    mockApi({
      "/user/profile/completeness": { body: COMPLETE },
      "/user/profile": { body: PROFILE },
      "/risk/analyze": { status: 201, body: ASSESSMENT },
      "/risk/latest": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
      "/portfolio/current": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
      "/prediction": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
    });
    renderDashboard();

    await user.click(await screen.findByRole("button", { name: /run risk assessment/i }));
    expect(await screen.findByText("0.476")).toBeInTheDocument();
  });
});

describe("Dashboard — failure messages", () => {
  it("explains a rate limit rather than showing a generic failure", async () => {
    // /risk/analyze shares a 10 req/min budget; "Request failed" tells a user
    // nothing about what to do, where "wait a minute" tells them exactly.
    const user = userEvent.setup();
    mockApi({
      "/user/profile/completeness": { body: COMPLETE },
      "/user/profile": { body: PROFILE },
      "/risk/analyze": {
        status: 429,
        body: { error: { code: "rate_limited", message: "Rate limit exceeded.", fields: null } },
      },
      "/risk/latest": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
      "/portfolio/current": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
      "/prediction": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
    });
    renderDashboard();

    await user.click(await screen.findByRole("button", { name: /run risk assessment/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/rate-limited.*wait a minute/i);
  });

  it("explains a missing production model", async () => {
    const user = userEvent.setup();
    mockApi({
      "/user/profile/completeness": { body: COMPLETE },
      "/user/profile": { body: PROFILE },
      "/risk/analyze": {
        status: 503,
        body: { error: { code: "model_unavailable", message: "No production model.", fields: null } },
      },
      "/risk/latest": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
      "/portfolio/current": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
      "/prediction": { status: 404, body: { error: { code: "x", message: "none", fields: null } } },
    });
    renderDashboard();

    await user.click(await screen.findByRole("button", { name: /run risk assessment/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not available yet/i);
  });
});
