import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AppLayout } from "../components/Layout";
import { InlineDisclaimer } from "../components/Disclaimer";
import { EmptyState } from "../components/EmptyState";
import { Metric } from "../components/Metric";
import { RiskBadge } from "../components/RiskBadge";
import { risk as riskApi } from "../api/resources";
import type { RiskAssessment } from "../api/types";
import { dateTime, decimal, percent } from "../lib/format";

/**
 * §14's Risk Profile page: the full factor breakdown behind the classification.
 *
 * The dashboard shows the score, the category and the top three factors, because
 * FR-15 requires them there without navigation. This page is the depth — each
 * factor's contribution as a share of the total, and the honest note about what
 * the model actually is.
 */

export default function RiskProfile() {
  const [assessment, setAssessment] = useState<RiskAssessment | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    riskApi
      .latest()
      .catch(() => null)
      .then(setAssessment)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <AppLayout>
        <p className="text-ink-muted">Loading your risk profile…</p>
      </AppLayout>
    );
  }

  if (!assessment) {
    return (
      <AppLayout>
        <h1 className="text-2xl font-semibold tracking-tight">Risk profile</h1>
        <div className="mt-6">
          <EmptyState
            title="No risk assessment yet"
            description="Once your financial profile is complete you can run an assessment from the dashboard."
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

  const total = assessment.top_factors.reduce((sum, factor) => sum + factor.contribution, 0);

  return (
    <AppLayout>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Risk profile</h1>
        <RiskBadge category={assessment.risk_category} />
      </div>
      <p className="mt-1 text-[13px] text-ink-muted">
        Assessed {dateTime(assessment.created_at)} · model {assessment.model_version}
      </p>

      <section className="mt-6 rounded border border-line bg-white p-5">
        <dl className="flex flex-wrap gap-x-10 gap-y-4">
          <Metric
            label="Risk score"
            value={decimal(assessment.risk_score, 3)}
            hint="0 to 1, higher means greater assessed capacity for risk"
            emphasis
          />
          <Metric label="Category" value={<RiskBadge category={assessment.risk_category} size="sm" />} />
        </dl>
      </section>

      <section className="mt-6 rounded border border-line bg-white p-5" aria-labelledby="factors-heading">
        <h2 id="factors-heading" className="text-[15px] font-semibold">
          Contributing factors
        </h2>
        <ul className="mt-4 space-y-4">
          {assessment.top_factors.map((factor) => {
            const share = total > 0 ? factor.contribution / total : 0;
            return (
              <li key={factor.factor}>
                <div className="flex items-baseline justify-between gap-4">
                  <span className="text-[14px] font-medium capitalize">{factor.factor}</span>
                  <span className="font-mono text-[13px] tabular-nums text-ink-muted">
                    {percent(share)} of the top factors
                  </span>
                </div>
                {/* The bar reinforces the number; the number is the source of truth,
                    so the bar being invisible costs nothing (§16.5). */}
                <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded bg-line" aria-hidden="true">
                  <div className="h-full rounded bg-accent" style={{ width: `${share * 100}%` }} />
                </div>
                <p className="mt-1.5 text-[13px] leading-relaxed text-ink-soft">{factor.detail}</p>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="mt-6 rounded border border-line bg-white p-5">
        <h2 className="text-[15px] font-semibold">How this is calculated</h2>
        <p className="mt-2 max-w-prose text-[13px] leading-relaxed text-ink-soft">
          Your classification comes from a Random Forest trained on a documented scoring rubric that
          weighs your stated risk appetite most heavily, followed by your investment horizon, age,
          savings relative to income, experience and financial literacy. The score shown is the
          rubric's own value rather than a model probability, so it does not shift when the model is
          retrained.
        </p>
        <p className="mt-2 max-w-prose text-[13px] leading-relaxed text-ink-muted">
          Because the model learns a rule this project wrote, its accuracy measures agreement with
          that rule — not whether the rule is right about people. It has not been reviewed by a
          qualified financial professional.
        </p>
      </section>

      <div className="mt-6">
        <InlineDisclaimer />
      </div>
    </AppLayout>
  );
}
