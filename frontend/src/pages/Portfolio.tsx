import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AppLayout } from "../components/Layout";
import { InlineDisclaimer } from "../components/Disclaimer";
import { EmptyState } from "../components/EmptyState";
import { Metric } from "../components/Metric";
import { RiskBadge } from "../components/RiskBadge";
import { AllocationChart } from "../components/charts/AllocationChart";
import { ClassBreakdown } from "../components/charts/ClassBreakdown";
import { BacktestChart } from "../components/charts/BacktestChart";
import { portfolio as portfolioApi } from "../api/resources";
import { ApiError } from "../api/types";
import type { Backtest, Portfolio as PortfolioModel } from "../api/types";
import { date as formatDate, dateTime, decimal, humanise, percent, signedPercent } from "../lib/format";

/**
 * FR-12 — allocation, expected return, expected risk, and history.
 *
 * FR-12 also lists "portfolio value". There is none: this system recommends
 * allocations and never holds or tracks money (PRD §5), so a value would have to be
 * invented from an assumed starting balance. The page says that plainly rather than
 * showing a fabricated figure — see the note under the metrics.
 *
 * The backtest section is FR-12's "historical performance" and §23's "the user can
 * view portfolio performance and/or backtest results". Until M6 the backtest existed
 * only as a CLI command, which satisfied §19 and left that line of the definition of
 * done unmet — the metrics were computed and no user could see them.
 */

export default function PortfolioPage() {
  const [current, setCurrent] = useState<PortfolioModel | null>(null);
  const [history, setHistory] = useState<PortfolioModel[]>([]);
  const [backtest, setBacktest] = useState<Backtest | null>(null);
  const [backtestNote, setBacktestNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      portfolioApi.current().catch(() => null),
      portfolioApi.history({ limit: 10 }).catch(() => ({ data: [], next_cursor: null })),
    ])
      .then(([latest, past]) => {
        setCurrent(latest);
        setHistory(past.data);
      })
      .finally(() => setLoading(false));

    // Separate from the pair above: the backtest simulates a year of daily returns
    // for every holding, so making the allocation wait on it would hold the whole
    // page for the slowest thing on it.
    portfolioApi
      .backtest()
      .then(setBacktest)
      .catch((error: unknown) => {
        setBacktestNote(
          error instanceof ApiError && error.status === 503
            ? error.message
            : "The backtest could not be run. Try again.",
        );
      });
  }, []);

  if (loading) {
    return (
      <AppLayout>
        <p className="text-ink-muted">Loading your portfolio…</p>
      </AppLayout>
    );
  }

  if (!current) {
    return (
      <AppLayout>
        <h1 className="text-2xl font-semibold tracking-tight">Portfolio</h1>
        <div className="mt-6">
          <EmptyState
            title="No portfolio yet"
            description="Portfolios are generated from your risk classification and current market data."
            action={
              <Link to="/dashboard" className="btn-primary inline-block">
                Go to the dashboard
              </Link>
            }
          />
        </div>
      </AppLayout>
    );
  }

  const bands = current.objective?.class_bands;
  const notes = current.objective?.notes ?? [];

  return (
    <AppLayout>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Portfolio</h1>
        <RiskBadge category={current.risk_category} />
      </div>
      <p className="mt-1 text-[13px] text-ink-muted">
        Generated {dateTime(current.created_at)} · model {current.model_version}
      </p>

      <section className="mt-6 rounded border border-line bg-white p-5">
        <dl className="flex flex-wrap gap-x-10 gap-y-4">
          <Metric
            label="Expected return"
            value={percent(current.expected_return)}
            hint="Annualised estimate from historical data — not a forecast"
            emphasis
          />
          <Metric
            label="Expected risk"
            value={percent(current.expected_risk)}
            hint="Annualised volatility estimate"
            emphasis
          />
          <Metric label="Holdings" value={current.holdings.length} />
          {/* FR-12 lists portfolio value; this system has none to report. Saying so
              is the honest option — the alternative is inventing a balance. */}
          <Metric
            label="Portfolio value"
            value={null}
            unavailableReason="WealthPilotX does not hold funds or track holdings, so there is no balance to report."
          />
        </dl>
      </section>

      <div className="mt-6 grid gap-6 md:grid-cols-2">
        <section className="rounded border border-line bg-white p-5" aria-labelledby="alloc-heading">
          <h2 id="alloc-heading" className="text-[15px] font-semibold">
            Allocation
          </h2>
          <AllocationChart holdings={current.holdings} height={240} />
        </section>

        <section className="rounded border border-line bg-white p-5" aria-labelledby="class-heading">
          <h2 id="class-heading" className="text-[15px] font-semibold">
            By asset class
          </h2>
          <p className="mt-1 text-[12px] text-ink-muted">
            Bars show the allocation; the amber markers show the cap the optimiser worked under.
          </p>
          <ClassBreakdown holdings={current.holdings} bands={bands} height={210} />
        </section>
      </div>

      <section className="mt-6" aria-labelledby="holdings-heading">
        <h2 id="holdings-heading" className="text-[15px] font-semibold">
          Holdings and reasons
        </h2>
        <div className="mt-3 overflow-x-auto rounded border border-line bg-white">
          <table aria-label="Recommended holdings" className="w-full text-left text-[13px]">
            <caption className="sr-only">
              Every holding with its weight, asset class and the reason it was recommended.
            </caption>
            <thead className="border-b border-line text-[12px] uppercase tracking-wide text-ink-muted">
              <tr>
                <th scope="col" className="px-4 py-2.5 font-medium">Symbol</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Class</th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">Weight</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Why</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {current.holdings.map((holding) => (
                <tr key={holding.symbol}>
                  <th scope="row" className="whitespace-nowrap px-4 py-3 text-left font-mono font-medium">
                    {holding.recommendation_id ? (
                      <Link
                        to={`/recommendation/${holding.recommendation_id}`}
                        className="text-accent-dark underline underline-offset-2"
                      >
                        {holding.symbol}
                      </Link>
                    ) : (
                      holding.symbol
                    )}
                  </th>
                  <td className="whitespace-nowrap px-4 py-3 text-ink-muted">
                    {humanise(holding.asset_class)}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right font-mono tabular-nums">
                    {percent(holding.weight)}
                  </td>
                  <td className="px-4 py-3 text-ink-soft">{holding.reason ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {notes.length > 0 && (
        <section className="mt-6 rounded border border-line bg-white p-5" aria-labelledby="why-heading">
          <h2 id="why-heading" className="text-[15px] font-semibold">
            Why these weights
          </h2>
          <p className="mt-1.5 text-[13px] leading-relaxed text-ink-soft">
            The weights were produced by a mean-variance optimiser under the constraints below, not
            selected from a preset allocation.
          </p>
          <ul className="mt-3 space-y-1.5">
            {notes.map((note) => (
              <li key={note} className="text-[13px] leading-relaxed text-ink-muted">
                — {note}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-6 rounded border border-line bg-white p-5" aria-labelledby="backtest-heading">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 id="backtest-heading" className="text-[15px] font-semibold">
            Historical simulation
          </h2>
          {backtest && (
            <p className="font-mono text-[12px] text-ink-muted">
              {formatDate(backtest.start)} — {formatDate(backtest.end)} ·{" "}
              {backtest.rebalances} rebalances
            </p>
          )}
        </div>

        {backtestNote ? (
          <p className="mt-3 max-w-prose text-[13px] leading-relaxed text-ink-soft" role="note">
            {backtestNote}
          </p>
        ) : !backtest ? (
          <p className="mt-3 text-[13px] text-ink-muted">Running the simulation…</p>
        ) : (
          <>
            <p className="mt-2 max-w-prose text-[13px] leading-relaxed text-ink-soft">
              How this allocation would have behaved over a period the models never saw. It is a
              simulation over historical prices, not a record of money that was invested — nobody
              held these positions and no returns were realised.
            </p>

            <div className="mt-4 overflow-x-auto">
              <table
                aria-label="Backtest metrics, portfolio against benchmark"
                className="w-full min-w-[30rem] border-collapse text-left"
              >
                <thead>
                  <tr className="border-b border-line text-[12px] uppercase tracking-wide text-ink-muted">
                    <th scope="col" className="py-2 pr-4 font-medium">Metric</th>
                    <th scope="col" className="py-2 pr-4 font-medium">Portfolio</th>
                    <th scope="col" className="py-2 font-medium">{backtest.benchmark_symbol}</th>
                  </tr>
                </thead>
                <tbody>
                  {(
                    [
                      // Returns and drawdown are signed quantities — the direction is
                      // the point. Volatility is a magnitude, so "+6.8%" would imply
                      // a gain of volatility, and a Sharpe ratio is not a percentage
                      // at all.
                      ["Total return", "total_return", "signed"],
                      ["Annualised return", "annualised_return", "signed"],
                      ["Volatility", "volatility", "magnitude"],
                      ["Sharpe ratio", "sharpe_ratio", "ratio"],
                      ["Maximum drawdown", "max_drawdown", "signed"],
                    ] as const
                  ).map(([label, key, kind]) => {
                    const mine = backtest.portfolio[key];
                    const theirs = backtest.benchmark[key];
                    const render = (v: number) =>
                      kind === "ratio"
                        ? decimal(v, 3)
                        : kind === "magnitude"
                          ? percent(v)
                          : signedPercent(v);
                    return (
                      <tr key={key} className="border-b border-line/60">
                        <th scope="row" className="py-2.5 pr-4 text-[14px] font-normal">
                          {label}
                        </th>
                        <td className="py-2.5 pr-4 font-mono text-[13px] tabular-nums">
                          {render(mine)}
                        </td>
                        <td className="py-2.5 font-mono text-[13px] tabular-nums text-ink-muted">
                          {render(theirs)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="mt-5">
              <BacktestChart
                portfolio={backtest.equity_curve}
                benchmark={backtest.benchmark_curve}
                benchmarkSymbol={backtest.benchmark_symbol}
              />
            </div>

            <p className="mt-4 max-w-prose text-[12px] leading-relaxed text-ink-muted">
              {/* §19 requires the cost assumption to be reported, not merely applied. */}
              Costs assumed: {backtest.transaction_cost_bps} bps per side on turnover, rebalanced
              every 21 trading days — {percent(backtest.total_costs)} of total drag over the window.
              {backtest.training_end && (
                <>
                  {" "}
                  The window starts after {formatDate(backtest.training_end)}, the last date the
                  prediction model was trained on, so this measures out-of-sample behaviour rather
                  than memorisation.
                </>
              )}
            </p>
          </>
        )}
      </section>

      {history.length > 1 && (
        <section className="mt-6" aria-labelledby="history-heading">
          <h2 id="history-heading" className="text-[15px] font-semibold">
            Earlier portfolios
          </h2>
          <ul className="mt-3 divide-y divide-line rounded border border-line bg-white">
            {history.slice(1).map((past) => (
              <li key={past.id} className="flex flex-wrap items-baseline justify-between gap-3 px-4 py-3">
                <span className="text-[13px] text-ink-soft">{dateTime(past.created_at)}</span>
                <span className="flex items-baseline gap-4 font-mono text-[13px] tabular-nums">
                  <span>{past.holdings.length} holdings</span>
                  <span>{percent(past.expected_return)} return</span>
                  <span>{percent(past.expected_risk)} risk</span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="mt-6">
        <InlineDisclaimer />
      </div>
    </AppLayout>
  );
}
