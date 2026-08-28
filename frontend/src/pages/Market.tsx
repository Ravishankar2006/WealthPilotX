import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AppLayout } from "../components/Layout";
import { InlineDisclaimer } from "../components/Disclaimer";
import { Metric } from "../components/Metric";
import { Trend } from "../components/Trend";
import { PriceChart } from "../components/charts/PriceChart";
import { market } from "../api/resources";
import type { Asset, MarketHistory, Prediction } from "../api/types";
import { date, humanise, percent } from "../lib/format";

/**
 * §14's Market Intelligence, with Asset Details as a panel rather than a route
 * (Phase 5 plan, judgment call 1).
 *
 * The selected symbol lives in the query string, so a particular asset is still a
 * shareable link — which is the only thing a separate route would have bought.
 */

const CLASSES = ["", "EQUITY", "BOND", "COMMODITY", "REAL_ESTATE"] as const;

export default function Market() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("symbol");
  const assetClass = params.get("class") ?? "";

  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    market
      .assets({ limit: 100, asset_class: assetClass || undefined })
      .then((page) => setAssets(page.data))
      .catch(() => setAssets([]))
      .finally(() => setLoading(false));
  }, [assetClass]);

  function select(symbol: string) {
    const next = new URLSearchParams(params);
    if (symbol === selected) next.delete("symbol");
    else next.set("symbol", symbol);
    setParams(next, { replace: true });
  }

  return (
    <AppLayout>
      <h1 className="text-2xl font-semibold tracking-tight">Market intelligence</h1>
      <p className="mt-1 text-[13px] text-ink-muted">
        The {assets.length || "tracked"} instruments this system follows. Select one to see its
        history and model prediction.
      </p>

      <div className="mt-5 flex flex-wrap gap-2" role="group" aria-label="Filter by asset class">
        {CLASSES.map((value) => (
          <button
            key={value || "all"}
            type="button"
            onClick={() => {
              const next = new URLSearchParams(params);
              if (value) next.set("class", value);
              else next.delete("class");
              setParams(next, { replace: true });
            }}
            aria-pressed={assetClass === value}
            className={`rounded border px-3 py-1 text-[13px] focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-1 ${
              assetClass === value
                ? "border-accent bg-accent-wash font-medium text-accent-dark"
                : "border-line bg-white text-ink-muted hover:text-ink"
            }`}
          >
            {value ? humanise(value) : "All"}
          </button>
        ))}
      </div>

      {/* Detail above the list, not expanded inside it.
          The inline version needed the page scrolled to the selected row, which
          raced the list render and the panel's own fetch: a `?symbol=` link landed
          900px above its own content and looked like it had done nothing. A
          master/detail layout puts what you asked for where you are already
          looking, and removes the timing problem rather than working around it. */}
      {selected && (
        <section className="mt-6 rounded border border-line bg-white" aria-live="polite">
          <AssetDetail symbol={selected} asset={assets.find((a) => a.symbol === selected)} />
        </section>
      )}

      {loading ? (
        <p className="mt-6 text-ink-muted">Loading assets…</p>
      ) : assets.length === 0 ? (
        <p className="mt-6 text-[13px] text-ink-muted">
          No assets are tracked yet. They are seeded by the ingestion job.
        </p>
      ) : (
        <ul className="mt-5 divide-y divide-line rounded border border-line bg-white">
          {assets.map((asset) => (
            <li key={asset.symbol}>
              <button
                type="button"
                onClick={() => select(asset.symbol)}
                aria-pressed={selected === asset.symbol}
                className={`flex w-full items-baseline justify-between gap-4 px-4 py-3 text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-accent ${
                  selected === asset.symbol ? "bg-accent-wash" : "hover:bg-ground"
                }`}
              >
                <span className="min-w-0">
                  <span className="font-mono text-[13px] font-medium">{asset.symbol}</span>
                  <span className="ml-3 truncate text-[13px] text-ink-muted">{asset.name}</span>
                </span>
                <span className="whitespace-nowrap text-[12px] uppercase tracking-wide text-ink-muted">
                  {humanise(asset.asset_class)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-6">
        <InlineDisclaimer />
      </div>
    </AppLayout>
  );
}

/** §14's Asset Details: price history plus the six FR-09 metrics. */
function AssetDetail({ symbol, asset }: { symbol: string; asset?: Asset }) {
  const [history, setHistory] = useState<MarketHistory | null>(null);
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      market.history(symbol, 180).catch(() => null),
      market.prediction(symbol).catch(() => null),
    ])
      .then(([bars, forecast]) => {
        setHistory(bars);
        setPrediction(forecast);
      })
      .finally(() => setLoading(false));
  }, [symbol]);

  if (loading) {
    return <p className="p-5 text-[13px] text-ink-muted">Loading {symbol}…</p>;
  }

  /** FR-09 requires an unavailable metric to be named with a reason, not blanked. */
  const unavailable = new Set(prediction?.unavailable ?? []);
  const reason = (metric: string) =>
    unavailable.has(metric)
      ? "Not enough price history for this measure yet."
      : prediction
        ? undefined
        : "No prediction has been generated for this asset yet.";

  return (
    <div className="p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="font-mono text-[17px] font-semibold">{symbol}</h2>
        {asset && <span className="text-[13px] text-ink-muted">{asset.name}</span>}
      </div>

      {history && history.data.length > 0 ? (
        <PriceChart bars={history.data} symbol={symbol} height={200} />
      ) : (
        <p className="text-[13px] text-ink-muted">No stored price history for {symbol}.</p>
      )}

      <dl className="mt-4 grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-3">
        <Metric
          label="Trend"
          value={
            prediction ? (
              <Trend
                direction={prediction.trend}
                value={prediction.predicted_return}
                horizonDays={prediction.horizon_days}
              />
            ) : null
          }
          unavailableReason={prediction ? undefined : reason("trend")}
        />
        <Metric
          label="Expected return"
          value={percent(prediction?.expected_return)}
          hint={prediction ? `Over ${prediction.horizon_days} trading days` : undefined}
          unavailableReason={prediction ? undefined : reason("expected_return")}
        />
        <Metric
          label="Confidence"
          value={percent(prediction?.confidence)}
          hint="Stability of the model's own estimate, not a probability of being right"
          unavailableReason={prediction ? undefined : reason("confidence")}
        />
        <Metric
          label="Volatility"
          value={percent(prediction?.volatility)}
          hint="Annualised, trailing 60 days"
          unavailableReason={reason("volatility")}
        />
        <Metric
          label="Momentum"
          value={percent(prediction?.momentum)}
          hint="Trailing 60-day return"
          unavailableReason={reason("momentum")}
        />
        <Metric
          label="Risk"
          value={percent(prediction?.risk_score)}
          hint="Volatility scaled to 0–1 for comparison across assets"
          unavailableReason={reason("risk_score")}
        />
      </dl>

      {prediction && (
        <p className="mt-4 text-[12px] text-ink-muted">
          Prediction from model {prediction.model_version}, as of {date(prediction.prediction_date)}.{" "}
          {prediction.disclaimer}
        </p>
      )}
    </div>
  );
}
