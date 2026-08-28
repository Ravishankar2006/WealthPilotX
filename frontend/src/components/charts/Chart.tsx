import { useEffect, useRef, useState } from "react";

/**
 * The single Plotly entry point (Phase 5 plan, decision 1).
 *
 * Plotly is loaded through a dynamic `import()`, so the ~1MB basic bundle stays out
 * of the initial payload: the page shell paints, then the chart chunk arrives.
 * §16.1 allows two seconds for the dashboard, and shipping a plotting library
 * before the first paint would spend most of that budget on code the user cannot
 * see yet.
 *
 * Wrapping `plotly.js` directly rather than using `react-plotly.js`: that package
 * imports Plotly at module scope, which defeats the whole point, and it is a thin
 * wrapper over the same two calls made below.
 *
 * Every chart takes a `description`. §16.5 requires that information not be
 * conveyed by colour alone, and a canvas is opaque to a screen reader no matter
 * what colours it uses — so the description is the text alternative, and it is a
 * required prop rather than an optional one so it cannot be quietly skipped.
 */

interface PlotlyApi {
  newPlot: (
    root: HTMLElement,
    data: unknown[],
    layout?: unknown,
    config?: unknown,
  ) => Promise<HTMLElement>;
  purge: (root: HTMLElement) => void;
}

let plotlyPromise: Promise<PlotlyApi> | null = null;

/**
 * One load, shared by every chart on the page.
 *
 * The bundle is UMD, so depending on how the bundler interops it the namespace may
 * carry the API directly or under `default`. Normalising here means a component
 * never has to care, and a change of bundler cannot silently break every chart.
 */
function loadPlotly(): Promise<PlotlyApi> {
  plotlyPromise ??= import("plotly.js-basic-dist-min").then((module) => {
    const candidate = (module as { default?: unknown }).default ?? module;
    return candidate as PlotlyApi;
  });
  return plotlyPromise;
}

/** Shared look, so twelve charts do not each invent their own axes. */
export const CHART_THEME = {
  font: { family: "IBM Plex Sans, system-ui, sans-serif", size: 12, color: "#5c6d6a" },
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  margin: { l: 52, r: 16, t: 12, b: 40 },
  xaxis: { gridcolor: "#eef3f2", zerolinecolor: "#dce5e3", automargin: true },
  yaxis: { gridcolor: "#eef3f2", zerolinecolor: "#dce5e3", automargin: true },
  hoverlabel: { bgcolor: "#151f1d", font: { color: "#ffffff", size: 12 } },
  showlegend: false,
} as const;

/**
 * A colourblind-safe qualitative palette (Okabe-Ito), used wherever a chart needs
 * to distinguish categories. Charts still label directly rather than relying on
 * the legend — see AllocationChart.
 */
export const SERIES_COLORS = [
  "#0e6b5b",
  "#e69f00",
  "#56b4e9",
  "#cc79a7",
  "#0072b2",
  "#d55e00",
  "#009e73",
  "#8f6408",
] as const;

export interface ChartProps {
  data: unknown[];
  layout?: Record<string, unknown>;
  /** Text alternative. Required — a canvas tells a screen reader nothing (§16.5). */
  description: string;
  height?: number;
  className?: string;
}

export function Chart({ data, layout = {}, description, height = 260, className }: ChartProps) {
  const node = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;

    loadPlotly()
      .then((Plotly) => {
        if (cancelled || !node.current) return;
        setReady(true);
        // No mode bar: it offers export and zoom controls that add clutter to a
        // dashboard and are not part of any requirement here.
        return Plotly.newPlot(node.current, data, { ...CHART_THEME, ...layout, height }, {
          displayModeBar: false,
          responsive: true,
        });
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });

    return () => {
      cancelled = true;
      const element = node.current;
      if (element && ready) {
        void loadPlotly().then((Plotly) => Plotly.purge(element));
      }
    };
    // `data` and `layout` are fresh objects each render; charts here are driven by
    // fetched data, so re-plotting when that data changes is exactly right.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(data), JSON.stringify(layout), height]);

  if (failed) {
    // A chart that fails to load must not take the panel with it. The description
    // carries the same information in words.
    return (
      <p className={`text-[13px] text-ink-muted ${className ?? ""}`} role="note">
        Chart could not be displayed. {description}
      </p>
    );
  }

  return (
    <figure className={className}>
      <div ref={node} style={{ height }} role="img" aria-label={description} />
      {/* Visible to screen readers only: the figure's own alt text is on the div,
          but a longer description belongs in the document for anyone who wants it. */}
      <figcaption className="sr-only">{description}</figcaption>
    </figure>
  );
}
