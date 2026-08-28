import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AppLayout } from "../components/Layout";
import { InlineDisclaimer } from "../components/Disclaimer";
import { EmptyState } from "../components/EmptyState";
import { Metric } from "../components/Metric";
import { RiskBadge } from "../components/RiskBadge";
import { AllocationChart } from "../components/charts/AllocationChart";
import { ClassBreakdown } from "../components/charts/ClassBreakdown";
import { portfolio as portfolioApi } from "../api/resources";
import type { Portfolio as PortfolioModel } from "../api/types";
import { dateTime, humanise, percent } from "../lib/format";

/**
 * FR-12 — allocation, expected return, expected risk, and history.
 *
 * FR-12 also lists "portfolio value". There is none: this system recommends
 * allocations and never holds or tracks money (PRD §5), so a value would have to be
 * invented from an assumed starting balance. The page says that plainly rather than
 * showing a fabricated figure — see the note under the metrics.
 */

export default function PortfolioPage() {
  const [current, setCurrent] = useState<PortfolioModel | null>(null);
  const [history, setHistory] = useState<PortfolioModel[]>([]);
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
          <table className="w-full text-left text-[13px]">
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
