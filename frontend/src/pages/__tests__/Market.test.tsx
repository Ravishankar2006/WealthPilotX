import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import Market from "../Market";
import { AuthProvider } from "../../context/AuthContext";
import { mockApi, PREDICTION } from "../../test/api-mock";

vi.mock("../../components/charts/PriceChart", () => ({
  PriceChart: () => <div data-testid="price-chart" />,
}));

const ASSETS = {
  data: [
    { id: "a1", symbol: "SPY", name: "SPDR S&P 500 ETF Trust", asset_type: "ETF", asset_class: "EQUITY", currency: "USD", exchange: "NYSEARCA", is_active: true },
    { id: "a2", symbol: "AGG", name: "iShares Core U.S. Aggregate Bond ETF", asset_type: "ETF", asset_class: "BOND", currency: "USD", exchange: "NYSEARCA", is_active: true },
  ],
  next_cursor: null,
};

const HISTORY = {
  asset: ASSETS.data[0],
  data: [
    { date: "2026-08-28", open: "500", high: "505", low: "498", close: "502", adj_close: "502", volume: 1000 },
    { date: "2026-08-27", open: "495", high: "501", low: "494", close: "500", adj_close: "500", volume: 1000 },
  ],
  next_cursor: null,
};

function renderPage(initial = "/market") {
  return render(
    <MemoryRouter initialEntries={[initial]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider>
        <Market />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("Market intelligence", () => {
  it("lists the tracked universe", async () => {
    mockApi({ "/market/assets": { body: ASSETS } });
    renderPage();

    expect(await screen.findByText("SPY")).toBeInTheDocument();
    expect(screen.getByText("AGG")).toBeInTheDocument();
  });

  it("opens an asset's detail with its prediction", async () => {
    const user = userEvent.setup();
    mockApi({
      "/market/assets": { body: ASSETS },
      "/market/SPY/prediction": { body: PREDICTION },
      "/market/SPY": { body: HISTORY },
    });
    renderPage();

    await user.click(await screen.findByRole("button", { name: /SPY/ }));

    expect(await screen.findByText(/upward/i)).toBeInTheDocument();
    expect(screen.getByText("72.0%")).toBeInTheDocument();
  });

  it("names an unavailable metric instead of blanking it (FR-09)", async () => {
    const user = userEvent.setup();
    mockApi({
      "/market/assets": { body: ASSETS },
      "/market/AGG/prediction": { status: 404, body: { error: { code: "no_prediction", message: "none", fields: null } } },
      "/market/AGG": { body: { ...HISTORY, asset: ASSETS.data[1] } },
    });
    renderPage();

    await user.click(await screen.findByRole("button", { name: /AGG/ }));

    // "Not available" with a reason — never a dash that reads as zero.
    expect((await screen.findAllByText(/not available/i)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/no prediction has been generated/i).length).toBeGreaterThan(0);
  });

  it("deep-links to a symbol through the query string", async () => {
    mockApi({
      "/market/assets": { body: ASSETS },
      "/market/SPY/prediction": { body: PREDICTION },
      "/market/SPY": { body: HISTORY },
    });
    renderPage("/market?symbol=SPY");

    expect(await screen.findByTestId("price-chart")).toBeInTheDocument();
  });

  it("shows the detail above the list rather than inside it", async () => {
    // The detail used to expand inline, which meant a `?symbol=` link landed 900px
    // above its own content on a 32-row list and looked like it had done nothing.
    // Scrolling to it raced both the list render and the panel's own fetch; putting
    // the detail first removes the timing problem instead of working around it.
    mockApi({
      "/market/assets": { body: ASSETS },
      "/market/SPY/prediction": { body: PREDICTION },
      "/market/SPY": { body: HISTORY },
    });
    const { container } = renderPage("/market?symbol=SPY");

    const chart = await screen.findByTestId("price-chart");
    const list = container.querySelector("ul");
    expect(list).not.toBeNull();

    // The chart must come before the list in document order.
    expect(chart.compareDocumentPosition(list!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
