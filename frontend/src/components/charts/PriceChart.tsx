import { Chart, CHART_THEME, SERIES_COLORS } from "./Chart";
import { percent } from "../../lib/format";
import type { PriceBar } from "../../api/types";

/**
 * Adjusted-close history for one symbol.
 *
 * A single series, so colour distinguishes nothing and the accessibility burden is
 * carried by the axis labels and the description — which states the direction and
 * the range in words, so the chart is not the only way to learn what it shows.
 */

export function PriceChart({
  bars,
  symbol,
  height = 240,
}: {
  bars: PriceBar[];
  symbol: string;
  height?: number;
}) {
  if (bars.length < 2) {
    return (
      <p className="py-8 text-center text-[13px] text-ink-muted">
        Not enough price history to draw a chart for {symbol}.
      </p>
    );
  }

  // The API returns newest-first; a time series has to be drawn oldest-first.
  const ordered = [...bars].reverse();
  const dates = ordered.map((bar) => bar.date);
  const closes = ordered.map((bar) => Number(bar.adj_close));

  const first = closes[0];
  const last = closes[closes.length - 1];
  const change = first > 0 ? (last - first) / first : 0;

  return (
    <Chart
      height={height}
      description={
        `${symbol} adjusted close from ${dates[0]} to ${dates[dates.length - 1]}: ` +
        `${first.toFixed(2)} to ${last.toFixed(2)}, a change of ${percent(change)}. ` +
        `Range over the period ${Math.min(...closes).toFixed(2)} to ${Math.max(...closes).toFixed(2)}.`
      }
      data={[
        {
          x: dates,
          y: closes,
          type: "scatter",
          mode: "lines",
          line: { color: SERIES_COLORS[0], width: 2 },
          // No area fill. `fill: "tozeroy"` forces Plotly to extend the y-axis down
          // to zero so the fill has somewhere to land — which flattens the series
          // into a band across the top and hides every move that matters. An ETF
          // trading between 150 and 210 looked like a straight line.
          hovertemplate: "%{x}<br>%{y:.2f}<extra></extra>",
          name: symbol,
        },
      ]}
      layout={{
        ...CHART_THEME,
        // Not zero-based, and explicitly so: price history is about the shape of
        // the movement, and a zero baseline compresses it out of existence.
        yaxis: { ...CHART_THEME.yaxis, tickformat: ".0f", rangemode: "normal", autorange: true },
        xaxis: { ...CHART_THEME.xaxis, type: "date" },
      }}
    />
  );
}
