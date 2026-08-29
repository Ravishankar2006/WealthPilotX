import { Chart, CHART_THEME, SERIES_COLORS } from "./Chart";
import { percent } from "../../lib/format";
import type { EquityPoint } from "../../api/types";

/**
 * Growth of 1 unit, portfolio against benchmark (§19).
 *
 * Two series, so unlike `PriceChart` this one genuinely needs to distinguish them —
 * and §16.5 says colour cannot be the thing that does it. So the portfolio is a
 * solid line and the benchmark a dashed one, the legend is shown with both names,
 * and the description states each series' start and end value in words. Printed in
 * greyscale, or read by a screen reader, the chart still says which line is which.
 *
 * Indexed to 1.0 rather than plotted in currency: the product does not know what
 * anyone invested and must not imply it does. A y-axis in dollars would be a
 * fabricated balance, which is the thing the Portfolio page refuses to show.
 */

export function BacktestChart({
  portfolio,
  benchmark,
  benchmarkSymbol,
  height = 260,
}: {
  portfolio: EquityPoint[];
  benchmark: EquityPoint[];
  benchmarkSymbol: string;
  height?: number;
}) {
  if (portfolio.length < 2) {
    return (
      <p className="py-8 text-center text-[13px] text-ink-muted">
        Not enough history in this window to draw a curve.
      </p>
    );
  }

  const growth = (points: EquityPoint[]) =>
    points.length ? points[points.length - 1].value - 1 : 0;

  return (
    <Chart
      height={height}
      description={
        `Growth of one unit from ${portfolio[0].date} to ${portfolio[portfolio.length - 1].date}. ` +
        `The portfolio ends at ${portfolio[portfolio.length - 1].value.toFixed(3)}, a change of ` +
        `${percent(growth(portfolio))}. ${benchmarkSymbol} ends at ` +
        `${(benchmark[benchmark.length - 1]?.value ?? 1).toFixed(3)}, a change of ` +
        `${percent(growth(benchmark))}. This is a historical simulation, not realised returns.`
      }
      data={[
        {
          x: portfolio.map((p) => p.date),
          y: portfolio.map((p) => p.value),
          type: "scatter",
          mode: "lines",
          line: { color: SERIES_COLORS[0], width: 2 },
          hovertemplate: "%{x}<br>portfolio %{y:.3f}<extra></extra>",
          name: "Portfolio",
        },
        {
          x: benchmark.map((p) => p.date),
          y: benchmark.map((p) => p.value),
          type: "scatter",
          mode: "lines",
          // Dashed, not merely a second colour — see the note above.
          line: { color: SERIES_COLORS[1], width: 2, dash: "dot" },
          hovertemplate: `%{x}<br>${benchmarkSymbol} %{y:.3f}<extra></extra>`,
          name: benchmarkSymbol,
        },
      ]}
      layout={{
        ...CHART_THEME,
        showlegend: true,
        legend: { orientation: "h", y: -0.18, x: 0, font: { size: 12 } },
        margin: { ...CHART_THEME.margin, b: 56 },
        // Not zero-based: both curves start at 1.0, so a zero baseline would push
        // the entire signal into the top few pixels — the same mistake the price
        // chart shipped with in M5.
        yaxis: { ...CHART_THEME.yaxis, tickformat: ".2f", rangemode: "normal", autorange: true },
        xaxis: { ...CHART_THEME.xaxis, type: "date" },
      }}
    />
  );
}
