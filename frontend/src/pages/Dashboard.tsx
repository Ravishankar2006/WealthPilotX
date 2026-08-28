import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AppLayout } from "../components/Layout";
import { InlineDisclaimer } from "../components/Disclaimer";
import { api } from "../api/client";
import type { FinancialProfile, ProfileCompleteness } from "../api/types";

/**
 * Milestone 1 shell.
 *
 * The seven elements FR-15 requires — risk score, risk profile, market outlook,
 * recommended portfolio, expected return, expected risk, explanations — all depend
 * on models that arrive in Milestones 3 and 4. This shows profile state and names
 * what is still to come rather than mocking numbers that would look real.
 */

const PENDING = [
  { label: "Risk classification", milestone: "M3", requirement: "FR-03" },
  { label: "Market outlook", milestone: "M3", requirement: "FR-08" },
  { label: "Recommended portfolio", milestone: "M4", requirement: "FR-10" },
  { label: "Expected return and risk", milestone: "M4", requirement: "FR-11" },
  { label: "Recommendation explanations", milestone: "M5", requirement: "FR-13" },
];

export default function Dashboard() {
  const [profile, setProfile] = useState<FinancialProfile | null>(null);
  const [status, setStatus] = useState<ProfileCompleteness | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.get<FinancialProfile>("/user/profile").catch(() => null),
      api.get<ProfileCompleteness>("/user/profile/completeness").catch(() => null),
    ])
      .then(([p, s]) => {
        setProfile(p);
        setStatus(s);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <AppLayout>
        <p className="text-ink-muted">Loading…</p>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>

      {!status?.complete ? (
        <div className="mt-6 rounded border border-line bg-white p-5">
          <h2 className="text-[15px] font-semibold">Finish your financial profile</h2>
          <p className="mt-1.5 max-w-prose text-[14px] leading-relaxed text-ink-muted">
            {status && status.missing_fields.length > 0
              ? `Still needed: ${status.missing_fields.join(", ").replace(/_/g, " ")}.`
              : "Your profile is what the risk assessment reads. It takes about two minutes."}
          </p>
          <Link to="/onboarding" className="btn-primary mt-4 inline-block">
            Complete profile
          </Link>
        </div>
      ) : (
        <div className="mt-6 rounded border border-line bg-white p-5">
          <div className="flex items-baseline justify-between gap-4">
            <h2 className="text-[15px] font-semibold">Your profile</h2>
            <Link to="/onboarding" className="text-[13px] text-accent-dark underline underline-offset-2">
              Edit
            </Link>
          </div>
          <dl className="mt-4 grid gap-x-8 gap-y-3 sm:grid-cols-2">
            {profile &&
              [
                ["Age", String(profile.age)],
                ["Horizon", `${profile.investment_horizon} years`],
                ["Risk appetite", profile.risk_appetite.toLowerCase()],
                ["Goal", profile.investment_goal.toLowerCase().replace(/_/g, " ")],
                ["Experience", profile.experience.toLowerCase()],
                ["Financial literacy", profile.financial_literacy.toLowerCase()],
              ].map(([label, value]) => (
                <div key={label} className="flex justify-between gap-4 border-b border-line pb-2">
                  <dt className="text-[13px] text-ink-muted">{label}</dt>
                  <dd className="text-[14px] font-medium capitalize">{value}</dd>
                </div>
              ))}
          </dl>
          <p className="mt-4 text-[13px] text-ink-muted">
            Income and savings are stored encrypted and are intentionally not displayed here.
          </p>
        </div>
      )}

      <section className="mt-8">
        <h2 className="text-[15px] font-semibold">Arriving in later milestones</h2>
        <ul className="mt-3 divide-y divide-line rounded border border-line bg-white">
          {PENDING.map((item) => (
            <li key={item.label} className="flex items-center justify-between gap-4 px-5 py-3">
              <span className="text-[14px] text-ink-soft">{item.label}</span>
              <span className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">
                {item.requirement} · {item.milestone}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <div className="mt-8">
        <InlineDisclaimer />
      </div>
    </AppLayout>
  );
}
