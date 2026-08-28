import { Chart, CHART_THEME, SERIES_COLORS } from "./Chart";
import { percent } from "../../lib/format";
import type { Holding } from "../../api/types";

/**
 * Portfolio weights as a donut.
 *
 * Labelled directly on each slice rather than through a legend (§16.5): a legend
 * maps colour to meaning, which is precisely the dependency the requirement is
 * about. With direct labels the chart survives greyscale, and the accompanying
 * table carries the same numbers for anyone who cannot use the chart at all.
 */

export function AllocationChart({ holdings, height = 260 }: { holdings: Holding[]; height?: number }) {
  if (holdings.length === 0) return null;

  const labels = holdings.map((holding) => holding.symbol);
  const values = holdings.map((holding) => Number(holding.weight));

  const summary = holdings
    .map((holding) => `${holding.symbol} ${percent(holding.weight)}`)
    .join(", ");

  return (
    <Chart
      height={height}
      description={`Portfolio allocation across ${holdings.length} holdings: ${summary}.`}
      data={[
        {
          labels,
          values,
          type: "pie",
          hole: 0.55,
          textinfo: "label+percent",
          textposition: "outside",
          automargin: true,
          marker: {
            colors: labels.map((_, index) => SERIES_COLORS[index % SERIES_COLORS.length]),
            // A white gap between slices keeps them distinguishable when the
            // colours themselves are not (printed, or in greyscale).
            line: { color: "#ffffff", width: 2 },
          },
          hovertemplate: "%{label}<br>%{percent}<extra></extra>",
          sort: false,
        },
      ]}
      layout={{ ...CHART_THEME, margin: { l: 40, r: 40, t: 20, b: 20 }, showlegend: false }}
    />
  );
}
