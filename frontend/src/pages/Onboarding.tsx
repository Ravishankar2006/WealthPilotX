import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { AppLayout } from "../components/Layout";
import { InlineDisclaimer } from "../components/Disclaimer";
import { api } from "../api/client";
import { ApiError, type FinancialProfile } from "../api/types";

/** Mirrors app/schemas/profile.py. Kept flat so field-level API errors map by key. */
interface FormState {
  age: string;
  income: string;
  savings: string;
  risk_appetite: string;
  investment_goal: string;
  investment_horizon: string;
  experience: string;
  financial_literacy: string;
}

const EMPTY: FormState = {
  age: "",
  income: "",
  savings: "",
  risk_appetite: "MODERATE",
  investment_goal: "GROWTH",
  investment_horizon: "",
  experience: "BEGINNER",
  financial_literacy: "MEDIUM",
};

const SELECTS: Record<string, { value: string; label: string }[]> = {
  risk_appetite: [
    { value: "CONSERVATIVE", label: "Conservative — protect what I have" },
    { value: "MODERATE", label: "Moderate — balanced growth and safety" },
    { value: "AGGRESSIVE", label: "Aggressive — accept swings for growth" },
  ],
  investment_goal: [
    { value: "RETIREMENT", label: "Retirement" },
    { value: "GROWTH", label: "Growth" },
    { value: "WEALTH_CREATION", label: "Wealth creation" },
  ],
  experience: [
    { value: "NONE", label: "None" },
    { value: "BEGINNER", label: "Beginner" },
    { value: "INTERMEDIATE", label: "Intermediate" },
    { value: "ADVANCED", label: "Advanced" },
  ],
  financial_literacy: [
    { value: "LOW", label: "Still learning the basics" },
    { value: "MEDIUM", label: "Comfortable with the fundamentals" },
    { value: "HIGH", label: "Confident with financial concepts" },
  ],
};

const LABELS: Record<keyof FormState, string> = {
  age: "Age",
  income: "Annual income",
  savings: "Current savings",
  risk_appetite: "Risk appetite",
  investment_goal: "Primary goal",
  investment_horizon: "Investment horizon (years)",
  experience: "Investment experience",
  financial_literacy: "Financial literacy",
};

export default function Onboarding() {
  const navigate = useNavigate();
  const [form, setForm] = useState<FormState>(EMPTY);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Editing an existing profile is the same form, pre-filled.
    api
      .get<FinancialProfile>("/user/profile")
      .then((profile) =>
        setForm({
          age: String(profile.age),
          income: profile.income,
          savings: profile.savings,
          risk_appetite: profile.risk_appetite,
          investment_goal: profile.investment_goal,
          investment_horizon: String(profile.investment_horizon),
          experience: profile.experience,
          financial_literacy: profile.financial_literacy,
        }),
      )
      .catch(() => {
        /* 404 simply means it hasn't been created yet */
      })
      .finally(() => setLoading(false));
  }, []);

  function update(field: keyof FormState, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
    setFieldErrors((prev) => {
      if (!prev[field]) return prev;
      const next = { ...prev };
      delete next[field];
      return next;
    });
    setSaved(false);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setFieldErrors({});
    setSubmitting(true);
    try {
      await api.put<FinancialProfile>("/user/profile", {
        ...form,
        age: Number(form.age),
        investment_horizon: Number(form.investment_horizon),
      });
      setSaved(true);
      navigate("/dashboard");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
        // Bind the §13.1 `fields` object straight onto the inputs.
        if (err.fields) {
          setFieldErrors(
            Object.fromEntries(Object.entries(err.fields).map(([k, v]) => [k, v[0]])),
          );
        }
      } else {
        setError("Could not reach the server. Try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <AppLayout>
        <p className="text-ink-muted">Loading your profile…</p>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <h1 className="text-2xl font-semibold tracking-tight">Your financial profile</h1>
      <p className="mt-2 max-w-prose text-[15px] leading-relaxed text-ink-muted">
        This is what the risk assessment reads. Income and savings are encrypted before
        they are stored, and are never written to logs.
      </p>

      <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-5" noValidate>
        {error && (
          <p role="alert" className="rounded border-l-2 border-danger bg-red-50 px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
        {saved && (
          <p role="status" className="rounded border-l-2 border-accent bg-accent-wash px-3 py-2 text-sm text-accent-dark">
            Profile saved.
          </p>
        )}

        <div className="grid gap-5 sm:grid-cols-2">
          {(["age", "investment_horizon", "income", "savings"] as const).map((field) => (
            <div key={field}>
              <label htmlFor={field} className="field-label">
                {LABELS[field]}
              </label>
              <input
                id={field}
                name={field}
                type="number"
                inputMode="decimal"
                min={field === "age" ? 18 : 0}
                step={field === "income" || field === "savings" ? "0.01" : "1"}
                required
                aria-invalid={Boolean(fieldErrors[field])}
                className={`field-input ${fieldErrors[field] ? "field-input-error" : ""}`}
                value={form[field]}
                onChange={(e) => update(field, e.target.value)}
              />
              {fieldErrors[field] && <p className="field-error">{fieldErrors[field]}</p>}
            </div>
          ))}
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          {(
            ["risk_appetite", "investment_goal", "experience", "financial_literacy"] as const
          ).map((field) => (
            <div key={field}>
              <label htmlFor={field} className="field-label">
                {LABELS[field]}
              </label>
              <select
                id={field}
                name={field}
                className={`field-input ${fieldErrors[field] ? "field-input-error" : ""}`}
                value={form[field]}
                onChange={(e) => update(field, e.target.value)}
              >
                {SELECTS[field].map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              {fieldErrors[field] && <p className="field-error">{fieldErrors[field]}</p>}
            </div>
          ))}
        </div>

        <InlineDisclaimer>
          Your answers are self-reported and are not verified against any external record.
          WealthPilotX uses them for educational analysis only.
        </InlineDisclaimer>

        <button type="submit" className="btn-primary self-start" disabled={submitting}>
          {submitting ? "Saving…" : "Save profile"}
        </button>
      </form>
    </AppLayout>
  );
}
