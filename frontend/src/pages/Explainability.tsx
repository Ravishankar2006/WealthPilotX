import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AppLayout } from "../components/Layout";
import { InlineDisclaimer } from "../components/Disclaimer";
import { EmptyState } from "../components/EmptyState";
import { Metric } from "../components/Metric";
import { RiskBadge } from "../components/RiskBadge";
import { market, risk as riskApi } from "../api/resources";
import { ApiError } from "../api/types";
import type { Asset, PredictionExplanation, RiskAssessment } from "../api/types";
import { date, decimal, percent, signedPercent } from "../lib/format";

/**
 * §14's Explainability page — FR-13's advanced half.
 *
 * Two panels, and the difference between them is the point of the page:
 *
 * **The market prediction** is decomposed with TreeSHAP, because a gradient-boosted
 * ensemble over twenty correlated features has no rule to read off. Every
 * contribution is in the units of the prediction, so they can be added up.
 *
 * **The risk score** is not decomposed with SHAP, because it does not need to be:
 * the score served to the user is the rubric's own weighted sum, so its per-factor
 * contributions are exact and already computed. An approximation of that would be
 * less accurate while looking more sophisticated. The page says so out loud, where
 * a reader can check it, rather than leaving the asymmetry to look like an
 * oversight.
 */

const DEFAULT_SYMBOL = "SPY";

/** Bars are drawn to the largest magnitude on screen, not to a fixed scale. */
function barWidth(value: number, largest: number): string {
  if (largest <= 0) return "0%";
  return `${Math.min(100, (Math.abs(value) / largest) * 100)}%`;
}

function describeFailure(error: unknown): string {
  if (error instanceof ApiError && error.status === 503) {
    return (
      error.message ||
      "The model that produced this prediction can no longer be loaded, so it cannot be explained."
    );
  }
  if (error instanceof ApiError && error.status === 429) {
    return "Too many requests just now. Wait a minute and try again.";
  }
  return "Could not load the explanation. Try again.";
}

export default function Explainability() {
  const [params, setParams] = useSearchParams();
  const symbol = (params.get("symbol") ?? DEFAULT_SYMBOL).toUpperCase();

  const [assets, setAssets] = useState<Asset[]>([]);
  const [assessment, setAssessment] = useState<RiskAssessment | null>(null);
  const [explanation, setExplanation] = useState<PredictionExplanation | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([market.assets({ limit: 100 }).catch(() => null), riskApi.latest().catch(() => null)])
      .then(([assetPage, latest]) => {
        if (cancelled) return;
        setAssets(assetPage?.data ?? []);
        setAssessment(latest);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setFailure(null);
    setExplanation(null);
    market
      .explanation(symbol)
      .then((result) => {
        if (!cancelled) setExplanation(result);
      })
      .catch((error: unknown) => {
        if (!cancelled) setFailure(describeFailure(error));
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const largest = explanation
    ? Math.max(...explanation.contributions.map((item) => Math.abs(item.contribution)), 0)
    : 0;

  return (
    <AppLayout>
      <h1 className="text-2xl font-semibold tracking-tight">Explainability</h1>
      <p className="mt-2 max-w-prose text-[15px] leading-relaxed text-ink-muted">
        What moved a prediction, and what moved your risk score. Both are model outputs for
        education and research, not recommendations to buy or sell anything.
      </p>

      <section
        className="mt-6 rounded border border-line bg-white p-5"
        aria-labelledby="prediction-heading"
      >
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 id="prediction-heading" className="text-[15px] font-semibold">
            Market prediction — feature contributions
          </h2>
          <label className="text-[13px] text-ink-muted">
            Asset{" "}
            <select
              className="ml-1 rounded border border-line bg-white px-2 py-1 text-[13px] text-ink"
              value={symbol}
              onChange={(event) => setParams({ symbol: event.target.value }, { replace: true })}
            >
              {(assets.length ? assets.map((a) => a.symbol) : [symbol]).map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        </div>

        {failure ? (
          <p className="mt-4 text-[14px] text-ink-soft" role="alert">
            {failure}
          </p>
        ) : !explanation ? (
          <div className="mt-4">
            <EmptyState
              title={`No explainable prediction for ${symbol}`}
              description="An explanation needs a stored prediction and the model version that produced it. Once the prediction job has run for this asset, its contributions appear here."
              action={
                <Link to="/market" className="btn-secondary inline-block">
                  Browse market data
                </Link>
              }
            />
          </div>
        ) : (
          <>
            <dl className="mt-4 flex flex-wrap gap-x-10 gap-y-4">
              <Metric
                label="Predicted return"
                value={signedPercent(explanation.predicted_return, 2)}
                hint={`over ${explanation.horizon_days} trading days`}
                emphasis
              />
              <Metric
                label="Baseline"
                value={signedPercent(explanation.base_value, 2)}
                hint="the model's average output before any feature moves it"
              />
              <Metric
                label="Prediction date"
                value={date(explanation.prediction_date)}
                hint={`model ${explanation.model_version}`}
              />
            </dl>

            {!explanation.reproduced && (
              <p className="mt-4 rounded border-l-2 border-warn bg-amber-50/70 px-3 py-2 text-[13px] leading-relaxed text-ink-soft">
                This model no longer reproduces the stored prediction from the same inputs. The
                contributions below describe what it does now, which is not exactly the number that
                was served.
              </p>
            )}

            <ul className="mt-5 space-y-3">
              {explanation.contributions.map((item) => (
                <li key={item.feature}>
                  <div className="flex items-baseline justify-between gap-4">
                    <span className="text-[14px]">{item.label}</span>
                    <span className="font-mono text-[13px] tabular-nums">
                      {/* The sign and the word both carry the direction, so the bar's
                          side is never the only thing saying it (§16.5). */}
                      {item.direction === "increases" ? "▲" : "▼"}{" "}
                      {signedPercent(item.contribution, 2)}
                    </span>
                  </div>
                  {/* Diverging bars around a centre line: contributions have signs,
                      and a left-anchored bar would make a −3% look like a +3%. */}
                  <div className="mt-1.5 flex h-1.5 w-full items-stretch" aria-hidden="true">
                    <div className="flex w-1/2 justify-end bg-line/40">
                      {item.direction === "decreases" && (
                        <div
                          className="h-full bg-[#7d281b]"
                          style={{ width: barWidth(item.contribution, largest) }}
                        />
                      )}
                    </div>
                    <div className="flex w-1/2 justify-start bg-line/40">
                      {item.direction === "increases" && (
                        <div
                          className="h-full bg-[#14513a]"
                          style={{ width: barWidth(item.contribution, largest) }}
                        />
                      )}
                    </div>
                  </div>
                  <p className="mt-1 text-[12px] text-ink-muted">
                    {item.value === null
                      ? "This input had no value on that date; the model still accounts for its absence."
                      : `Value that day: ${decimal(item.value, 4)}`}
                  </p>
                </li>
              ))}
            </ul>

            <p className="mt-5 max-w-prose text-[13px] leading-relaxed text-ink-muted">
              Showing the {explanation.contributions_shown} largest of{" "}
              {explanation.contributions_total} features. Across all of them the contributions and
              the baseline add up exactly to the predicted return; the {explanation.contributions_total -
                explanation.contributions_shown}{" "}
              not shown are the remainder.
            </p>
          </>
        )}

        <div className="mt-5">
          <InlineDisclaimer />
        </div>
      </section>

      <section className="mt-6 rounded border border-line bg-white p-5" aria-labelledby="risk-heading">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 id="risk-heading" className="text-[15px] font-semibold">
            Your risk score — exact factor contributions
          </h2>
          {assessment && <RiskBadge category={assessment.risk_category} size="sm" />}
        </div>

        {loading ? (
          <p className="mt-4 text-[14px] text-ink-muted">Loading…</p>
        ) : !assessment ? (
          <div className="mt-4">
            <EmptyState
              title="No risk assessment yet"
              description="Run an assessment from the dashboard and its factor breakdown appears here."
              action={
                <Link to="/dashboard" className="btn-primary inline-block">
                  Go to the dashboard
                </Link>
              }
            />
          </div>
        ) : (
          <>
            <p className="mt-2 max-w-prose text-[13px] leading-relaxed text-ink-soft">
              These are not estimates. Your risk score is a weighted sum of six factors, so each
              factor's contribution to it is known exactly — which is why this panel does not use
              SHAP. Approximating a number that is already exact would look more sophisticated and
              be less accurate.
            </p>
            <ul className="mt-4 space-y-3">
              {assessment.top_factors.map((factor) => (
                <li key={factor.factor}>
                  <div className="flex items-baseline justify-between gap-4">
                    <span className="text-[14px] capitalize">{factor.factor}</span>
                    <span className="font-mono text-[13px] tabular-nums">
                      {decimal(factor.contribution, 3)} of {decimal(assessment.risk_score, 3)}
                    </span>
                  </div>
                  <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded bg-line" aria-hidden="true">
                    <div
                      className="h-full rounded bg-accent"
                      style={{
                        width: percent(
                          factor.contribution / Math.max(Number(assessment.risk_score), 1e-9),
                        ),
                      }}
                    />
                  </div>
                  <p className="mt-1 text-[13px] leading-relaxed text-ink-soft">{factor.detail}</p>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </AppLayout>
  );
}
