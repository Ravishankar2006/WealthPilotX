import { useEffect, useState } from "react";
import { AppLayout } from "../components/Layout";
import { fairness as fairnessApi } from "../api/resources";
import type { FairnessDimension, FairnessGroup, FairnessReport } from "../api/types";
import { decimal, percent } from "../lib/format";

/**
 * §14's Fairness page — FR-14.
 *
 * The design problem here is the opposite of every other page's. Elsewhere the job
 * is to show the numbers clearly; here the job is to *not* show numbers that were
 * suppressed, and to make the absence legible rather than mysterious. A group below
 * the minimum size renders as "not reported", never as a dash and never as zero:
 * a rate of 0% on a group of three is a statement about three identifiable people.
 *
 * The interpretation note is not decoration either. Age and income are inputs to
 * the risk rubric by design, so disparity across those bands is expected. A page
 * that showed a red "flagged" chip without saying so would manufacture a finding
 * out of the product working as documented.
 */

const DIMENSIONS_EXPECTED_TO_DIFFER = new Set(["age_band", "income_band"]);

function SuppressedCell() {
  return (
    <span className="text-[13px] italic text-ink-muted" title="Below the minimum group size">
      not reported
    </span>
  );
}

function RiskSplit({ group }: { group: FairnessGroup }) {
  if (group.suppressed || !group.risk_distribution) return <SuppressedCell />;

  const order = ["LOW", "MEDIUM", "HIGH"] as const;
  return (
    <span className="font-mono text-[13px] tabular-nums">
      {order.map((category, index) => (
        <span key={category}>
          {index > 0 && <span className="text-ink-muted"> · </span>}
          <span className="text-ink-muted">{category[0]}</span>{" "}
          {percent(group.risk_distribution?.[category] ?? 0, 0)}
        </span>
      ))}
    </span>
  );
}

function Dimension({ dimension }: { dimension: FairnessDimension }) {
  const expected = DIMENSIONS_EXPECTED_TO_DIFFER.has(dimension.dimension);

  return (
    <section className="mt-6 rounded border border-line bg-white p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-[15px] font-semibold">{dimension.label}</h2>
        {dimension.disparity && (
          <span
            className={`rounded px-2 py-0.5 text-[12px] font-medium ${
              dimension.disparity.flagged
                ? "bg-amber-50 text-warn"
                : "bg-accent-wash text-accent-dark"
            }`}
          >
            {/* The word, not only the colour (§16.5). */}
            {dimension.disparity.flagged ? "Below four-fifths" : "Within four-fifths"} ·{" "}
            {decimal(dimension.disparity.ratio, 2)}
          </span>
        )}
      </div>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[34rem] border-collapse text-left">
          <thead>
            <tr className="border-b border-line text-[12px] uppercase tracking-wide text-ink-muted">
              <th scope="col" className="py-2 pr-4 font-medium">
                Group
              </th>
              <th scope="col" className="py-2 pr-4 font-medium">
                Users
              </th>
              <th scope="col" className="py-2 pr-4 font-medium">
                Risk split
              </th>
              <th scope="col" className="py-2 pr-4 font-medium">
                Mean score
              </th>
              <th scope="col" className="py-2 font-medium">
                Mean equity weight
              </th>
            </tr>
          </thead>
          <tbody>
            {dimension.groups.map((group) => (
              <tr key={group.group} className="border-b border-line/60 align-baseline">
                <th scope="row" className="py-2.5 pr-4 text-[14px] font-normal">
                  {group.group}
                </th>
                <td className="py-2.5 pr-4 font-mono text-[13px] tabular-nums">{group.size}</td>
                <td className="py-2.5 pr-4">
                  <RiskSplit group={group} />
                </td>
                <td className="py-2.5 pr-4 font-mono text-[13px] tabular-nums">
                  {group.mean_risk_score === null ? (
                    <SuppressedCell />
                  ) : (
                    decimal(group.mean_risk_score, 3)
                  )}
                </td>
                <td className="py-2.5 font-mono text-[13px] tabular-nums">
                  {group.suppressed ? (
                    <SuppressedCell />
                  ) : group.mean_equity_weight === null ? (
                    <span className="text-[13px] italic text-ink-muted">no portfolios yet</span>
                  ) : (
                    percent(group.mean_equity_weight)
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {dimension.disparity ? (
        <p className="mt-3 max-w-prose text-[13px] leading-relaxed text-ink-soft">
          {dimension.disparity.metric}: {percent(dimension.disparity.lowest_rate)} in{" "}
          {dimension.disparity.lowest_group} against {percent(dimension.disparity.highest_rate)} in{" "}
          {dimension.disparity.highest_group}.{" "}
          {expected
            ? "This attribute feeds the risk rubric by design, so a difference here is expected — the question is whether it is larger than the rubric's declared weighting can account for."
            : "This attribute does not feed the rubric directly, so a large gap here is worth investigating."}
        </p>
      ) : (
        <p className="mt-3 max-w-prose text-[13px] leading-relaxed text-ink-muted">
          {dimension.note}
        </p>
      )}
    </section>
  );
}

export default function Fairness() {
  const [report, setReport] = useState<FairnessReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fairnessApi
      .report()
      .then((result) => {
        if (!cancelled) setReport(result);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <AppLayout>
      <h1 className="text-2xl font-semibold tracking-tight">Fairness</h1>
      <p className="mt-2 max-w-prose text-[15px] leading-relaxed text-ink-muted">
        How this instance's model outputs are distributed across groups. These attributes are used
        for auditing only — none of them is a decision input beyond what the documented risk rubric
        already weighs.
      </p>

      {loading ? (
        <p className="mt-6 text-ink-muted">Loading the report…</p>
      ) : failed || !report ? (
        <p className="mt-6 text-[14px] text-ink-soft" role="alert">
          Could not load the fairness report. Try again.
        </p>
      ) : (
        <>
          <section className="mt-6 rounded border border-line bg-white p-5">
            <dl className="flex flex-wrap gap-x-10 gap-y-3">
              <div>
                <dt className="text-[12px] uppercase tracking-wide text-ink-muted">
                  Users with an assessment
                </dt>
                <dd className="mt-1 font-mono text-[17px] tabular-nums">{report.population}</dd>
              </div>
              <div>
                <dt className="text-[12px] uppercase tracking-wide text-ink-muted">
                  In reportable groups
                </dt>
                <dd className="mt-1 font-mono text-[17px] tabular-nums">
                  {report.reportable_population}
                </dd>
              </div>
              <div>
                <dt className="text-[12px] uppercase tracking-wide text-ink-muted">
                  Minimum group size
                </dt>
                <dd className="mt-1 font-mono text-[17px] tabular-nums">{report.min_group_size}</dd>
              </div>
            </dl>
            <p className="mt-4 max-w-prose text-[13px] leading-relaxed text-ink-soft">
              Any group with fewer than {report.min_group_size} users is withheld rather than
              rounded or zeroed. On a small population a group statistic describes identifiable
              people, so "not reported" here means exactly that — not that the value was zero.
            </p>
          </section>

          {report.dimensions.map((dimension) => (
            <Dimension key={dimension.dimension} dimension={dimension} />
          ))}

          <p className="mt-6 max-w-prose text-[13px] leading-relaxed text-ink-muted">
            {report.disclaimer}
          </p>
        </>
      )}
    </AppLayout>
  );
}
