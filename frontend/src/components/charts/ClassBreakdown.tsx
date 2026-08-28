import { Chart, CHART_THEME, SERIES_COLORS } from "./Chart";
import { humanise, percent } from "../../lib/format";
import type { ClassBand, Holding } from "../../api/types";

/**
 * Weight by asset class, against the constraint band that governed it.
 *
 * This is the chart that answers "why is this only 12% equities?" — the bar shows
 * where the optimiser landed and the marker shows the ceiling it was working
 * under. Without the band, a user reading a low equity weight has no way to tell
 * whether the optimiser chose it or was forced into it.
 */

export function ClassBreakdown({
  holdings,
  bands,
  height = 200,
}: {
  holdings: Holding[];
  bands?: Record<string, ClassBand>;
  height?: number;
}) {
  const byClass = new Map<string, number>();
  for (const holding of holdings) {
    byClass.set(holding.asset_class, (byClass.get(holding.asset_class) ?? 0) + Number(holding.weight));
  }
  if (byClass.size === 0) return null;

  const classes = [...byClass.keys()].sort();
  const weights = classes.map((name) => byClass.get(name) ?? 0);
  const caps = classes.map((name) => bands?.[name]?.cap ?? null);

  const described = classes
    .map((name, index) => {
      const cap = caps[index];
      const limit = cap === null ? "" : ` against a cap of ${percent(cap)}`;
      return `${humanise(name)} ${percent(weights[index])}${limit}`;
    })
    .join("; ");

  return (
    <Chart
      height={height}
      description={`Allocation by asset class: ${described}.`}
      data={[
        {
          x: classes.map(humanise),
          y: weights,
          type: "bar",
          marker: { color: SERIES_COLORS[0] },
          hovertemplate: "%{x}<br>%{y:.1%}<extra></extra>",
          name: "Allocation",
        },
        // The caps, drawn as markers rather than a second bar: a bar would read as
        // "we also hold this much", which is the opposite of what a ceiling means.
        ...(bands
          ? [
              {
                x: classes.map(humanise),
                y: caps.map((cap) => cap ?? 0),
                type: "scatter",
                mode: "markers",
                marker: { symbol: "line-ew-open", size: 26, color: "#8f6408", line: { width: 3 } },
                hovertemplate: "Cap %{y:.0%}<extra></extra>",
                name: "Cap",
              },
            ]
          : []),
      ]}
      layout={{
        ...CHART_THEME,
        yaxis: { ...CHART_THEME.yaxis, tickformat: ".0%", rangemode: "tozero" },
        bargap: 0.5,
      }}
    />
  );
}
