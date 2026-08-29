import { vi } from "vitest";

/**
 * Route-aware fetch stub.
 *
 * The dashboard issues five or six requests in parallel, so a single
 * `mockResolvedValue` cannot describe what the page should see — every endpoint
 * would return the same body. This matches on the path instead, and anything
 * unrouted throws, so a test can never accidentally pass because a call it forgot
 * to stub silently returned the wrong shape.
 */

type Route = Record<string, { status?: number; body?: unknown }>;

export function mockApi(routes: Route): void {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();

    // Longest match first, so "/market/SPY/prediction" beats "/market/SPY".
    const match = Object.keys(routes)
      .sort((a, b) => b.length - a.length)
      .find((path) => url.includes(path));

    if (!match) {
      throw new Error(`Test made an unrouted request to ${url}`);
    }

    const { status = 200, body = {} } = routes[match];
    return new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
}

export const DISCLAIMER =
  "Model outputs and past performance do not guarantee future results.";

export const PROFILE = {
  id: "p1",
  age: 34,
  income: "82000.00",
  savings: "25000.00",
  risk_appetite: "MODERATE",
  investment_goal: "GROWTH",
  investment_horizon: 15,
  experience: "BEGINNER",
  financial_literacy: "MEDIUM",
  created_at: "2026-08-01T10:00:00Z",
  updated_at: "2026-08-01T10:00:00Z",
};

export const COMPLETE = { complete: true, missing_fields: [] };

export const ASSESSMENT = {
  id: "r1",
  risk_category: "MEDIUM",
  risk_score: "0.47614",
  top_factors: [
    { factor: "stated risk appetite", contribution: 0.15, detail: "Your stated risk appetite moderately increases the assessed capacity for risk." },
    { factor: "investment horizon", contribution: 0.12, detail: "Your investment horizon strongly increases the assessed capacity for risk." },
    { factor: "age", contribution: 0.108, detail: "Your age moderately increases the assessed capacity for risk." },
  ],
  model_version: "v1",
  created_at: "2026-08-28T10:00:00Z",
  disclaimer: DISCLAIMER,
};

export const PORTFOLIO = {
  id: "pf1",
  risk_category: "MEDIUM",
  expected_return: "0.1961",
  expected_risk: "0.0842",
  model_version: "v1",
  created_at: "2026-08-28T11:00:00Z",
  holdings: [
    {
      symbol: "SPY",
      name: "SPDR S&P 500 ETF Trust",
      asset_class: "EQUITY",
      weight: "0.25000000",
      reason: "SPY is allocated 25.0% because its expected return is among the highest in the candidate set.",
      recommendation_id: "rec-1",
    },
    {
      symbol: "AGG",
      name: "iShares Core U.S. Aggregate Bond ETF",
      asset_class: "BOND",
      weight: "0.20000000",
      reason: "AGG is allocated 20.0% because its volatility is close to the level targeted for your risk profile.",
      recommendation_id: "rec-2",
    },
  ],
  objective: {
    risk_aversion: 5,
    max_weight_per_asset: 0.25,
    class_bands: { EQUITY: { floor: 0.35, cap: 0.65 }, BOND: { floor: 0.2, cap: 0.5 } },
    notes: ["Risk category MEDIUM sets a risk-aversion of 5.0 and a 25% cap on any single holding."],
    summary: "This 2-holding portfolio was optimised for a MEDIUM risk profile.",
  },
  explanation: "This 2-holding portfolio was optimised for a MEDIUM risk profile.",
  disclaimer: DISCLAIMER,
};

export const PREDICTION = {
  symbol: "SPY",
  prediction_date: "2026-08-28",
  horizon_days: 20,
  predicted_return: "0.02340000",
  trend: "UP",
  confidence: "0.7200",
  model_version: "v1",
  expected_return: "0.02340000",
  volatility: 0.1838,
  momentum: 0.0356,
  risk_score: 0.4596,
  unavailable: [],
  disclaimer: DISCLAIMER,
};

export const BACKTEST = {
  portfolio_id: "pf1",
  start: "2025-09-12",
  end: "2026-08-28",
  months_requested: 12,
  training_end: "2025-09-11",
  rebalances: 11,
  portfolio: {
    total_return: 0.1597,
    annualised_return: 0.161,
    volatility: 0.0683,
    sharpe_ratio: 2.0652,
    max_drawdown: -0.0367,
  },
  benchmark: {
    total_return: 0.1321,
    annualised_return: 0.1332,
    volatility: 0.1847,
    sharpe_ratio: 0.613,
    max_drawdown: -0.1347,
  },
  benchmark_symbol: "SPY",
  transaction_cost_bps: 10.0,
  total_costs: 0.001359,
  equity_curve: [
    { date: "2025-09-15", value: 1.0047 },
    { date: "2026-08-28", value: 1.1597 },
  ],
  benchmark_curve: [
    { date: "2025-09-15", value: 0.9981 },
    { date: "2026-08-28", value: 1.1321 },
  ],
  disclaimer: DISCLAIMER,
};
