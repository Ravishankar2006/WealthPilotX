import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PortfolioPage from "../Portfolio";
import { AuthProvider } from "../../context/AuthContext";
import { mockApi, PORTFOLIO } from "../../test/api-mock";

vi.mock("../../components/charts/AllocationChart", () => ({
  AllocationChart: () => <div data-testid="allocation-chart" />,
}));
vi.mock("../../components/charts/ClassBreakdown", () => ({
  ClassBreakdown: () => <div data-testid="class-chart" />,
}));

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
    mockApi({
      "/portfolio/history": { body: { data: [PORTFOLIO], next_cursor: null } },
      "/portfolio/current": { body: PORTFOLIO },
    });
    renderPage();

    expect(await screen.findByText("19.6%")).toBeInTheDocument();
    expect(screen.getByText("8.4%")).toBeInTheDocument();
    expect(screen.getByText(/not a forecast/i)).toBeInTheDocument();
  });

  it("says there is no portfolio value rather than inventing one", async () => {
    // FR-12 lists "portfolio value", but this system never holds or tracks money
    // (§5). Showing a balance would mean fabricating a starting figure.
    mockApi({
      "/portfolio/history": { body: { data: [PORTFOLIO], next_cursor: null } },
      "/portfolio/current": { body: PORTFOLIO },
    });
    renderPage();

    expect(await screen.findByText(/does not hold funds/i)).toBeInTheDocument();
  });

  it("lists every holding with its reason", async () => {
    mockApi({
      "/portfolio/history": { body: { data: [PORTFOLIO], next_cursor: null } },
      "/portfolio/current": { body: PORTFOLIO },
    });
    renderPage();

    const table = await screen.findByRole("table");
    expect(table).toHaveTextContent("SPY");
    expect(table).toHaveTextContent("AGG");
    expect(table).toHaveTextContent(/among the highest in the candidate set/i);
  });

  it("shows the constraints that produced the weights", async () => {
    mockApi({
      "/portfolio/history": { body: { data: [PORTFOLIO], next_cursor: null } },
      "/portfolio/current": { body: PORTFOLIO },
    });
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
