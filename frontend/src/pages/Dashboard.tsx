import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AppLayout } from "../components/Layout";
import { InlineDisclaimer } from "../components/Disclaimer";
import { EmptyState } from "../components/EmptyState";
import { Metric } from "../components/Metric";
import { RiskBadge } from "../components/RiskBadge";
import { Trend } from "../components/Trend";
import { AllocationChart } from "../components/charts/AllocationChart";
import { api } from "../api/client";
import { market, portfolio as portfolioApi, risk as riskApi } from "../api/resources";
import { ApiError } from "../api/types";
import type {
  FinancialProfile,
  Portfolio,
  Prediction,
  ProfileCompleteness,
  RiskAssessment,
} from "../api/types";
import { decimal, humanise, percent } from "../lib/format";

/**
 * FR-15 — all seven elements on one screen, without further navigation.
 *
 *   1 risk score · 2 risk profile · 3 market outlook · 4 recommended portfolio
 *   5 expected return · 6 expected risk · 7 recommendation explanations
 *
 * Everything is fetched in parallel. A 404 from `/risk/latest` or
 * `/portfolio/current` is an empty state rather than an error — it is the ordinary
 * condition for a new account, and treating it as a failure would make every user's
 * first visit look broken.
 *
 * **Nothing expensive runs on mount** (Phase 5 plan, decision 2). Both
 * `/risk/analyze` and `/portfolio/generate` run models and sit in the 10 req/min
 * bucket, so they fire only on an explicit click.
 */

// How many symbols the outlook strip covers. Bounded deliberately: one request per
// symbol, and §16.1 gives the whole dashboard two seconds.
const OUTLOOK_SYMBOLS = ["SPY", "QQQ", "AGG", "GLD"];

interface Outlook {
  symbol: string;
  prediction: Prediction | null;
}

export default function Dashboard() {
  const [profile, setProfile] = useState<FinancialProfile | null>(null);
  const [completeness, setCompleteness] = useState<ProfileCompleteness | null>(null);
  const [assessment, setAssessment] = useState<RiskAssessment | null>(null);
  const [currentPortfolio, setCurrentPortfolio] = useState<Portfolio | null>(null);
  const [outlook, setOutlook] = useState<Outlook[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<"risk" | "portfolio" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [profileResult, completenessResult, riskResult, portfolioResult] = await Promise.all([
      api.get<FinancialProfile>("/user/profile").catch(() => null),
      api.get<ProfileCompleteness>("/user/profile/completeness").catch(() => null),
      riskApi.latest().catch(() => null),
      portfolioApi.current().catch(() => null),
    ]);

    setProfile(profileResult);
    setCompleteness(completenessResult);
    setAssessment(riskResult);
    setCurrentPortfolio(portfolioResult);

    // Predictions are a nice-to-have on this screen: one slow or missing symbol
    // must not hold up the six elements that matter.
    const predictions = await Promise.all(
      OUTLOOK_SYMBOLS.map(async (symbol) => ({
        symbol,
        prediction: await market.prediction(symbol).catch(() => null),
      })),
    );
    setOutlook(predictions);
  }, []);

  useEffect(() => {
    load().finally(() => setLoading(false));
  }, [load]);

  async function run(kind: "risk" | "portfolio") {
    setRunning(kind);
    setError(null);
    try {
      if (kind === "risk") {
        setAssessment(await riskApi.analyze());
      } else {
        setCurrentPortfolio(await portfolioApi.generate());
      }
    } catch (caught) {
      setError(describeFailure(caught));
    } finally {
      setRunning(null);
    }
  }

  if (loading) {
    return (
      <AppLayout>
        <p className="text-ink-muted">Loading your dashboard…</p>
      </AppLayout>
    );
  }

  const profileComplete = completeness?.complete === true;

  return (
    <AppLayout>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        {assessment && <RiskBadge category={assessment.risk_category} />}
      </div>

      {error && (
        <p className="mt-4 rounded border border-danger/40 bg-danger/5 px-3 py-2 text-[13px] text-danger" role="alert">
          {error}
        </p>
      )}

      {!profileComplete ? (
        <div className="mt-6">
          <EmptyState
            title="Finish your financial profile"
            description={
              completeness && completeness.missing_fields.length > 0
                ? `Still needed: ${completeness.missing_fields.join(", ").replace(/_/g, " ")}. Everything else on this page reads from it.`
                : "Your profile is what the risk assessment reads. It takes about two minutes."
            }
            action={
              <Link to="/onboarding" className="btn-primary inline-block">
                Complete profile
              </Link>
            }
          />
        </div>
      ) : (
        <div className="mt-6 grid gap-6">
          <RiskPanel
            assessment={assessment}
            running={running === "risk"}
            onRun={() => run("risk")}
          />

          <PortfolioPanel
            portfolio={currentPortfolio}
            hasAssessment={Boolean(assessment)}
            running={running === "portfolio"}
            onRun={() => run("portfolio")}
          />

          <OutlookPanel outlook={outlook} />
        </div>
      )}

      {profile && (
        <p className="mt-8 text-[13px] text-ink-muted">
          Built from your profile: {profile.age} years old, {profile.investment_horizon}-year
          horizon, {humanise(profile.investment_goal).toLowerCase()} goal. Income and savings are
          stored encrypted and are deliberately not shown here.
        </p>
      )}

      <div className="mt-6">
        <InlineDisclaimer />
      </div>
    </AppLayout>
  );
}

/** FR-15 elements 1 and 2, plus the factors behind them. */
function RiskPanel({
  assessment,
  running,
  onRun,
}: {
  assessment: RiskAssessment | null;
  running: boolean;
  onRun: () => void;
}) {
  return (
    <section className="rounded border border-line bg-white p-5" aria-labelledby="risk-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="risk-heading" className="text-[15px] font-semibold">
          Risk profile
        </h2>
        {assessment && (
          <Link to="/risk" className="text-[13px] text-accent-dark underline underline-offset-2">
            Full breakdown
          </Link>
        )}
      </div>

      {!assessment ? (
        <div className="mt-4">
          <EmptyState
            title="No risk assessment yet"
            description="Your profile is complete, so this can run now. It classifies your capacity for risk and lists the factors that drove the result."
            action={
              <button type="button" className="btn-primary" onClick={onRun} disabled={running}>
                {running ? "Running…" : "Run risk assessment"}
              </button>
            }
          />
        </div>
      ) : (
        <>
          <dl className="mt-4 flex flex-wrap gap-x-10 gap-y-4">
            <Metric
              label="Risk score"
              value={decimal(assessment.risk_score, 3)}
              hint="0 to 1, higher means greater assessed capacity for risk"
              emphasis
            />
            <Metric
              label="Category"
              value={<RiskBadge category={assessment.risk_category} size="sm" />}
              hint={`Model ${assessment.model_version}`}
            />
          </dl>

          <h3 className="mt-5 text-[13px] font-medium text-ink-soft">
            What drove this classification
          </h3>
          <ol className="mt-2 space-y-2">
            {assessment.top_factors.map((factor, index) => (
              <li key={factor.factor} className="flex gap-3 text-[13px] leading-relaxed">
                <span className="font-mono text-ink-muted">{index + 1}.</span>
                <span className="text-ink-soft">{factor.detail}</span>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

/** FR-15 elements 4, 5, 6 and 7. */
function PortfolioPanel({
  portfolio,
  hasAssessment,
  running,
  onRun,
}: {
  portfolio: Portfolio | null;
  hasAssessment: boolean;
  running: boolean;
  onRun: () => void;
}) {
  return (
    <section className="rounded border border-line bg-white p-5" aria-labelledby="portfolio-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="portfolio-heading" className="text-[15px] font-semibold">
          Recommended portfolio
        </h2>
        {portfolio && (
          <Link to="/portfolio" className="text-[13px] text-accent-dark underline underline-offset-2">
            Full portfolio
          </Link>
        )}
      </div>

      {!portfolio ? (
        <div className="mt-4">
          <EmptyState
            title="No portfolio yet"
            description="A portfolio is generated from your risk classification, your goal and horizon, and current market data."
            blockedBy={
              hasAssessment ? undefined : "Run the risk assessment above first — the optimiser reads its result."
            }
            action={
              <button type="button" className="btn-primary" onClick={onRun} disabled={running}>
                {running ? "Generating…" : "Generate portfolio"}
              </button>
            }
          />
        </div>
      ) : (
        <>
          <dl className="mt-4 flex flex-wrap gap-x-10 gap-y-4">
            <Metric
              label="Expected return"
              value={percent(portfolio.expected_return)}
              hint="Annualised model estimate, not a forecast"
              emphasis
            />
            <Metric
              label="Expected risk"
              value={percent(portfolio.expected_risk)}
              hint="Annualised volatility estimate"
              emphasis
            />
            <Metric label="Holdings" value={portfolio.holdings.length} />
          </dl>

          <div className="mt-5 grid gap-5 md:grid-cols-[minmax(0,260px)_1fr] md:items-start">
            <AllocationChart holdings={portfolio.holdings} height={220} />

            <ol className="space-y-3">
              {portfolio.holdings.slice(0, 4).map((holding) => (
                <li key={holding.symbol} className="border-b border-line pb-3 last:border-0">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="font-mono text-[13px] font-medium">{holding.symbol}</span>
                    <span className="font-mono text-[13px] tabular-nums">
                      {percent(holding.weight)}
                    </span>
                  </div>
                  {/* FR-15 element 7 — the explanation travels with the holding. */}
                  {holding.reason && (
                    <p className="mt-1 text-[12px] leading-relaxed text-ink-muted">
                      {holding.reason}
                    </p>
                  )}
                </li>
              ))}
              {portfolio.holdings.length > 4 && (
                <li className="text-[13px]">
                  <Link to="/portfolio" className="text-accent-dark underline underline-offset-2">
                    {portfolio.holdings.length - 4} more holdings
                  </Link>
                </li>
              )}
            </ol>
          </div>
        </>
      )}
    </section>
  );
}

/** FR-15 element 3. */
function OutlookPanel({ outlook }: { outlook: Outlook[] }) {
  const withPredictions = outlook.filter((item) => item.prediction !== null);

  return (
    <section className="rounded border border-line bg-white p-5" aria-labelledby="outlook-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="outlook-heading" className="text-[15px] font-semibold">
          Market outlook
        </h2>
        <Link to="/market" className="text-[13px] text-accent-dark underline underline-offset-2">
          All markets
        </Link>
      </div>

      {withPredictions.length === 0 ? (
        <p className="mt-3 text-[13px] text-ink-muted">
          No predictions are available yet. They are produced by a scheduled job once a market model
          has been trained and promoted.
        </p>
      ) : (
        <ul className="mt-4 grid gap-3 sm:grid-cols-2">
          {withPredictions.map(({ symbol, prediction }) => (
            <li key={symbol} className="flex items-baseline justify-between gap-3 border-b border-line pb-2">
              <span className="font-mono text-[13px] font-medium">{symbol}</span>
              <Trend
                direction={prediction!.trend}
                value={prediction!.predicted_return}
                horizonDays={prediction!.horizon_days}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * Turn an API failure into something a person can act on.
 *
 * The 429 case matters most: `/risk/analyze` and `/portfolio/generate` share a
 * 10 req/min budget, and "Request failed" tells a user nothing about what to do
 * next, where "wait a minute" tells them exactly.
 */
function describeFailure(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.status === 429) {
      return "You have run this several times in quick succession. These calculations are rate-limited — please wait a minute and try again.";
    }
    if (caught.status === 503) {
      return "The model needed for this is not available yet. An administrator needs to train and promote one.";
    }
    return caught.message;
  }
  return "Something went wrong. Please try again.";
}
