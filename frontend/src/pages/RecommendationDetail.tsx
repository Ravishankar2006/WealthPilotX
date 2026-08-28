import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AppLayout } from "../components/Layout";
import { InlineDisclaimer } from "../components/Disclaimer";
import { Metric } from "../components/Metric";
import { portfolio as portfolioApi } from "../api/resources";
import { ApiError } from "../api/types";
import type { Explanation } from "../api/types";
import { dateTime, decimal, percent } from "../lib/format";

/**
 * §14's Recommendation Details, and FR-13's explanation surface.
 *
 * Another user's recommendation returns 404 rather than 403 (§16.2) — whether
 * someone else holds a recommendation is not ours to confirm — so this page treats
 * "not found" and "not yours" identically, which is exactly right.
 */

export default function RecommendationDetail() {
  const { id } = useParams<{ id: string }>();
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) return;
    portfolioApi
      .explanation(id)
      .then(setExplanation)
      .catch((caught: unknown) => {
        if (caught instanceof ApiError && caught.status === 404) setNotFound(true);
      })
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <AppLayout>
        <p className="text-ink-muted">Loading…</p>
      </AppLayout>
    );
  }

  if (notFound || !explanation) {
    return (
      <AppLayout>
        <h1 className="text-2xl font-semibold tracking-tight">Recommendation</h1>
        <p className="mt-3 text-[14px] text-ink-muted">
          No such recommendation for this account.
        </p>
        <Link to="/portfolio" className="btn-primary mt-5 inline-block">
          Back to your portfolio
        </Link>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <p className="text-[13px]">
        <Link to="/portfolio" className="text-accent-dark underline underline-offset-2">
          Portfolio
        </Link>
        <span className="mx-2 text-ink-muted">/</span>
        <span className="text-ink-muted">{explanation.symbol}</span>
      </p>

      <h1 className="mt-2 font-mono text-2xl font-semibold tracking-tight">{explanation.symbol}</h1>
      <p className="mt-1 text-[13px] text-ink-muted">
        Recommended {dateTime(explanation.created_at)} · model {explanation.model_version}
      </p>

      <section className="mt-6 rounded border border-line bg-white p-5">
        <dl className="flex flex-wrap gap-x-10 gap-y-4">
          <Metric
            label="Weight in portfolio"
            value={percent(explanation.weight)}
            emphasis
            unavailableReason={
              explanation.weight ? undefined : "This recommendation is not part of a stored portfolio."
            }
          />
          <Metric
            label="Suitability score"
            value={decimal(explanation.score, 4)}
            hint="Relative ranking within the candidate set, 0 to 1"
          />
        </dl>
      </section>

      <section className="mt-6 rounded border border-line bg-white p-5" aria-labelledby="why-heading">
        <h2 id="why-heading" className="text-[15px] font-semibold">
          Why {explanation.symbol}
        </h2>
        <p className="mt-2 max-w-prose text-[14px] leading-relaxed text-ink-soft">
          {explanation.reason}
        </p>
      </section>

      {explanation.portfolio_explanation && (
        <section className="mt-6 rounded border border-line bg-white p-5" aria-labelledby="context-heading">
          <h2 id="context-heading" className="text-[15px] font-semibold">
            The portfolio it belongs to
          </h2>
          <div className="mt-2 max-w-prose space-y-1.5 text-[13px] leading-relaxed text-ink-soft">
            {explanation.portfolio_explanation.split("\n").map((line) => (
              <p key={line}>{line}</p>
            ))}
          </div>
        </section>
      )}

      <div className="mt-6">
        <InlineDisclaimer />
      </div>
    </AppLayout>
  );
}
