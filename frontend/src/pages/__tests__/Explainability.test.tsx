import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Explainability from "../Explainability";
import { AuthProvider } from "../../context/AuthContext";
import { ASSESSMENT, DISCLAIMER, mockApi } from "../../test/api-mock";

const ASSETS = {
  data: [
    { id: "a1", symbol: "SPY", name: "SPDR S&P 500 ETF Trust", asset_type: "ETF", asset_class: "EQUITY", currency: "USD", exchange: "NYSEARCA", is_active: true },
  ],
  next_cursor: null,
};

const EXPLANATION = {
  symbol: "SPY",
  prediction_date: "2026-08-28",
  horizon_days: 20,
  model_version: "v3",
  predicted_return: 0.0234,
  base_value: 0.004,
  contributions: [
    { feature: "momentum_60", label: "Momentum (60-day)", value: 0.081, contribution: 0.0121, direction: "increases" },
    { feature: "rsi_14", label: "Relative strength (14-day)", value: 71.2, contribution: -0.0068, direction: "decreases" },
    { feature: "inflation", label: "Inflation", value: null, contribution: 0.0031, direction: "increases" },
  ],
  contributions_shown: 3,
  contributions_total: 19,
  reproduced: true,
  disclaimer: DISCLAIMER,
};

function renderPage(initial = "/explainability") {
  return render(
    <MemoryRouter initialEntries={[initial]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider>
        <Explainability />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const ROUTES = {
  "/market/assets": { body: ASSETS },
  "/market/SPY/prediction/explanation": { body: EXPLANATION },
  "/risk/latest": { body: ASSESSMENT },
};

describe("Explainability", () => {
  it("names the model version that produced the prediction", async () => {
    // §10.5 — a served result that cannot be traced to a model version is not
    // auditable, and the whole page is an audit surface.
    mockApi(ROUTES);
    renderPage();

    expect(await screen.findByText(/model v3/)).toBeInTheDocument();
  });

  it("shows each contribution with a glyph and a signed value, not colour alone", async () => {
    mockApi(ROUTES);
    renderPage();

    expect(await screen.findByText(/▲ \+1\.21%/)).toBeInTheDocument();
    expect(screen.getByText(/▼ -0\.68%/)).toBeInTheDocument();
  });

  it("says how many features are hidden so the arithmetic is not left mysterious", async () => {
    // base_value + shown does not equal the prediction, and a reader who checks
    // deserves to find out why from the page rather than from the source.
    mockApi(ROUTES);
    renderPage();

    expect(await screen.findByText(/Showing the 3 largest of 19 features/)).toBeInTheDocument();
  });

  it("distinguishes a missing input value from a zero one", async () => {
    mockApi(ROUTES);
    renderPage();

    expect(
      await screen.findByText(/This input had no value on that date/),
    ).toBeInTheDocument();
  });

  it("warns when the model no longer reproduces its own stored prediction", async () => {
    mockApi({
      ...ROUTES,
      "/market/SPY/prediction/explanation": { body: { ...EXPLANATION, reproduced: false } },
    });
    renderPage();

    expect(
      await screen.findByText(/no longer reproduces the stored prediction/),
    ).toBeInTheDocument();
  });

  it("carries the section 17.1 disclaimer", async () => {
    mockApi(ROUTES);
    renderPage();

    expect(await screen.findByRole("note")).toHaveTextContent(/do not guarantee future results/);
  });

  it("offers an empty state when there is no prediction to explain", async () => {
    mockApi({ ...ROUTES, "/market/SPY/prediction/explanation": { status: 404, body: { error: { code: "no_prediction", message: "none" } } } });
    renderPage();

    expect(await screen.findByText(/No explainable prediction for SPY/)).toBeInTheDocument();
  });

  it("reports an unloadable model rather than rendering an empty panel", async () => {
    mockApi({
      ...ROUTES,
      "/market/SPY/prediction/explanation": {
        status: 503,
        body: { error: { code: "explanation_unavailable", message: "The stored artifact for model version 'v3' could not be loaded." } },
      },
    });
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be loaded/);
  });

  it("explains why the risk panel does not use SHAP", async () => {
    // The asymmetry between the two panels is deliberate and would otherwise read
    // as an unfinished feature.
    mockApi(ROUTES);
    renderPage();

    expect(
      await screen.findByText(/Approximating a number that is already exact/),
    ).toBeInTheDocument();
  });

  it("honours a ?symbol= deep link", async () => {
    mockApi({
      ...ROUTES,
      "/market/AGG/prediction/explanation": { body: { ...EXPLANATION, symbol: "AGG", model_version: "v9" } },
    });
    renderPage("/explainability?symbol=AGG");

    expect(await screen.findByText(/model v9/)).toBeInTheDocument();
  });
});
