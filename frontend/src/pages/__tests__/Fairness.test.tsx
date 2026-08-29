import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Fairness from "../Fairness";
import { AuthProvider } from "../../context/AuthContext";
import { mockApi } from "../../test/api-mock";

/**
 * The tests that matter on this page are about what is *not* rendered.
 *
 * A suppressed group must never appear as a number — not as 0, not as a dash that
 * reads as 0. The backend already withholds the value; the page's job is to make
 * the withholding legible instead of rendering `null` into something that looks
 * like a measurement.
 */

function group(overrides: Record<string, unknown> = {}) {
  return {
    group: "30-44",
    size: 40,
    suppressed: false,
    risk_distribution: { LOW: 0.1, MEDIUM: 0.65, HIGH: 0.25 },
    mean_risk_score: 0.512,
    mean_equity_weight: 0.58,
    portfolio_rate: 0.9,
    ...overrides,
  };
}

const REPORT = {
  population: 60,
  reportable_population: 60,
  min_group_size: 20,
  dimensions: [
    {
      dimension: "age_band",
      label: "Age",
      groups: [
        group({ group: "18-29", size: 20, mean_risk_score: 0.71, risk_distribution: { LOW: 0, MEDIUM: 0.3, HIGH: 0.7 } }),
        group({ group: "30-44" }),
        group({
          group: "45-59",
          size: 4,
          suppressed: true,
          risk_distribution: null,
          mean_risk_score: null,
          mean_equity_weight: null,
          portfolio_rate: null,
        }),
        group({
          group: "60+",
          size: 0,
          suppressed: true,
          risk_distribution: null,
          mean_risk_score: null,
          mean_equity_weight: null,
          portfolio_rate: null,
        }),
      ],
      disparity: {
        metric: "HIGH risk classification rate",
        ratio: 0.357,
        lowest_group: "30-44",
        highest_group: "18-29",
        lowest_rate: 0.25,
        highest_rate: 0.7,
        flagged: true,
      },
      note: null,
    },
    {
      dimension: "experience",
      label: "Investment experience",
      groups: [group({ group: "NONE", size: 3, suppressed: true, risk_distribution: null, mean_risk_score: null, mean_equity_weight: null, portfolio_rate: null })],
      disparity: null,
      note: "Fewer than two groups reach the minimum size of 20, so no disparity ratio can be computed.",
    },
  ],
  disclaimer: "These are aggregate statistics over this instance's own users, published for auditing.",
};

function renderPage() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider>
        <Fairness />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("Fairness report", () => {
  it("renders a suppressed group as withheld, never as a number", async () => {
    mockApi({ "/fairness/report": { body: REPORT } });
    renderPage();

    const row = (await screen.findByRole("row", { name: /45-59/ })) as HTMLElement;

    // Three withheld cells: risk split, mean score, mean equity weight. The group
    // size itself is not suppressed — it is what justifies the suppression.
    expect(within(row).getAllByText("not reported")).toHaveLength(3);
    expect(within(row).queryByText("0")).not.toBeInTheDocument();
    expect(within(row).queryByText("0.0%")).not.toBeInTheDocument();
  });

  it("keeps an empty band in the table rather than dropping it", async () => {
    mockApi({ "/fairness/report": { body: REPORT } });
    renderPage();

    // A band that vanishes because nobody is in it makes the population look more
    // uniform than it is.
    expect(await screen.findByRole("row", { name: /60\+/ })).toBeInTheDocument();
  });

  it("states the disparity in words as well as a ratio", async () => {
    mockApi({ "/fairness/report": { body: REPORT } });
    renderPage();

    // §16.5: the flag must not be carried by the chip's colour alone.
    expect(await screen.findByText(/Below four-fifths/)).toBeInTheDocument();
    expect(screen.getByText(/0\.36/)).toBeInTheDocument();
  });

  it("says a difference across age is expected rather than implying a fault", async () => {
    mockApi({ "/fairness/report": { body: REPORT } });
    renderPage();

    expect(
      await screen.findByText(/feeds the risk rubric by design, so a difference here is expected/),
    ).toBeInTheDocument();
  });

  it("explains why a dimension has no ratio instead of leaving it blank", async () => {
    mockApi({ "/fairness/report": { body: REPORT } });
    renderPage();

    expect(
      await screen.findByText(/Fewer than two groups reach the minimum size of 20/),
    ).toBeInTheDocument();
  });

  it("explains what suppression means in the header", async () => {
    mockApi({ "/fairness/report": { body: REPORT } });
    renderPage();

    expect(
      await screen.findByText(/"not reported" here means exactly that — not that the value was zero/),
    ).toBeInTheDocument();
  });

  it("reports a failure rather than an empty table", async () => {
    mockApi({ "/fairness/report": { status: 500, body: { error: { code: "internal_error", message: "boom" } } } });
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(/Could not load the fairness report/);
  });

  it("renders an untouched instance without crashing", async () => {
    // The state every fresh deployment is in, and the one most likely to be
    // skipped in testing because it looks trivial.
    mockApi({
      "/fairness/report": {
        body: {
          population: 0,
          reportable_population: 0,
          min_group_size: 20,
          dimensions: [
            {
              dimension: "age_band",
              label: "Age",
              groups: [
                {
                  group: "18-29",
                  size: 0,
                  suppressed: true,
                  risk_distribution: null,
                  mean_risk_score: null,
                  mean_equity_weight: null,
                  portfolio_rate: null,
                },
              ],
              disparity: null,
              note: "Fewer than two groups reach the minimum size of 20, so no disparity ratio can be computed.",
            },
          ],
          disclaimer: "Aggregate statistics.",
        },
      },
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "Fairness" })).toBeInTheDocument();
    expect(screen.getAllByText("not reported").length).toBeGreaterThan(0);
  });
});
