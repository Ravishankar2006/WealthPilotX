import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PortfolioPage from "../Portfolio";
import { AuthProvider } from "../../context/AuthContext";
import { BACKTEST, mockApi, PORTFOLIO } from "../../test/api-mock";

vi.mock("../../components/charts/AllocationChart", () => ({
  AllocationChart: () => <div data-testid="allocation-chart" />,
}));
vi.mock("../../components/charts/ClassBreakdown", () => ({
  ClassBreakdown: () => <div data-testid="class-chart" />,
}));
vi.mock("../../components/charts/BacktestChart", () => ({
  BacktestChart: () => <div data-testid="backtest-chart" />,
}));

/** The three requests the page makes; individual tests override what they need. */
const ROUTES = {
  "/portfolio/history": { body: { data: [PORTFOLIO], next_cursor: null } },
  "/portfolio/backtest": { body: BACKTEST },
  "/portfolio/current": { body: PORTFOLIO },
};

function renderPage() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider>
        <PortfolioPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("Portfolio — FR-12", () => {
  it("shows expected return and risk as estimates rather than forecasts", async () => {
    mockApi(ROUTES);
    renderPage();

    expect(await screen.findByText("19.6%")).toBeInTheDocument();
    expect(screen.getByText("8.4%")).toBeInTheDocument();
    expect(screen.getByText(/not a forecast/i)).toBeInTheDocument();
  });

  it("says there is no portfolio value rather than inventing one", async () => {
    // FR-12 lists "portfolio value", but this system never holds or tracks money
    // (§5). Showing a balance would mean fabricating a starting figure.
    mockApi(ROUTES);
    renderPage();

    expect(await screen.findByText(/does not hold funds/i)).toBeInTheDocument();
  });

  it("lists every holding with its reason", async () => {
    mockApi(ROUTES);
    renderPage();

    const table = await screen.findByRole("table", { name: /recommended holdings/i });
    expect(table).toHaveTextContent("SPY");
    expect(table).toHaveTextContent("AGG");
    expect(table).toHaveTextContent(/among the highest in the candidate set/i);
  });

  it("shows the constraints that produced the weights", async () => {
    mockApi(ROUTES);
    renderPage();

    expect(await screen.findByText(/not selected from a preset allocation/i)).toBeInTheDocument();
    expect(screen.getByText(/25% cap on any single holding/i)).toBeInTheDocument();
  });

  it("offers a route forward when there is no portfolio", async () => {
    mockApi({
      "/portfolio/history": { body: { data: [], next_cursor: null } },
      "/portfolio/current": { status: 404, body: { error: { code: "no_portfolio", message: "none", fields: null } } },
    });
    renderPage();

    expect(await screen.findByText(/no portfolio yet/i)).toBeInTheDocument();
    // The nav also links to the dashboard, so match the call-to-action's own wording.
    expect(screen.getByRole("link", { name: /go to the dashboard/i })).toBeInTheDocument();
  });
});

describe("Portfolio — the backtest (§19, §23 item 9)", () => {
  it("shows all five metrics for the portfolio and the benchmark", async () => {
    mockApi(ROUTES);
    renderPage();

    const table = await screen.findByRole("table", { name: /backtest metrics/i });
    for (const metric of [
      "Total return",
      "Annualised return",
      "Volatility",
      "Sharpe ratio",
      "Maximum drawdown",
    ]) {
      expect(table).toHaveTextContent(metric);
    }
    // Portfolio and benchmark side by side — §19 requires the comparison, not just
    // the portfolio's own numbers.
    expect(table).toHaveTextContent("+16.0%");
    expect(table).toHaveTextContent("+13.2%");
    expect(table).toHaveTextContent("SPY");
  });

  it("renders volatility unsigned and returns signed", async () => {
    // Volatility is a magnitude; "+6.8%" would read as a gain of volatility.
    mockApi(ROUTES);
    renderPage();

    const table = await screen.findByRole("table", { name: /backtest metrics/i });
    expect(table).toHaveTextContent("6.8%");
    expect(table).not.toHaveTextContent("+6.8%");
    expect(table).toHaveTextContent("-3.7%");
  });

  it("reports the transaction-cost assumption, not just applies it", async () => {
    // §19 singles this out: results that look frictionless are misleading.
    mockApi(ROUTES);
    renderPage();

    expect(await screen.findByText(/10 bps per side on turnover/i)).toBeInTheDocument();
  });

  it("says the window starts after the model's training period", async () => {
    mockApi(ROUTES);
    renderPage();

    expect(
      await screen.findByText(/out-of-sample behaviour rather than memorisation/i),
    ).toBeInTheDocument();
  });

  it("calls it a simulation rather than realised performance", async () => {
    mockApi(ROUTES);
    renderPage();

    expect(
      await screen.findByText(/nobody held these positions and no returns were realised/i),
    ).toBeInTheDocument();
  });

  it("explains why there is no backtest instead of showing an empty panel", async () => {
    // A 503 means there is not enough out-of-sample history yet — an operational
    // state with an actionable message, not a failure to hide.
    const reason =
      "Only 12 days of price history fall outside the model's training window.";
    mockApi({
      ...ROUTES,
      "/portfolio/backtest": {
        status: 503,
        body: { error: { code: "backtest_unavailable", message: reason } },
      },
    });
    renderPage();

    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(
      screen.queryByRole("table", { name: /backtest metrics/i }),
    ).not.toBeInTheDocument();
  });
});
