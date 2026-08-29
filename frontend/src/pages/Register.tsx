import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PublicLayout } from "../components/Layout";
import { useAuth } from "../context/AuthContext";
import { ApiError } from "../api/types";

const MIN_PASSWORD_LENGTH = 12;

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [acceptedTerms, setAcceptedTerms] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setFieldErrors({});
    setSubmitting(true);
    try {
      await register(email, password);
      navigate("/onboarding", { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
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

  return (
    <PublicLayout>
      <h1 className="text-2xl font-semibold tracking-tight">Create your account</h1>
      <p className="mt-2 text-[15px] text-ink-muted">
        Already registered?{" "}
        <Link to="/login" className="text-accent-dark underline underline-offset-2">
          Sign in
        </Link>
        .
      </p>

      <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-4" noValidate>
        {error && (
          <p role="alert" className="rounded border-l-2 border-danger bg-red-50 px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}

        <div>
          <label htmlFor="email" className="field-label">
            Email
          </label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            aria-invalid={Boolean(fieldErrors.email)}
            className={`field-input ${fieldErrors.email ? "field-input-error" : ""}`}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          {fieldErrors.email && <p className="field-error">{fieldErrors.email}</p>}
        </div>

        <div>
          <label htmlFor="password" className="field-label">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD_LENGTH}
            aria-invalid={Boolean(fieldErrors.password)}
            aria-describedby="password-hint"
            className={`field-input ${fieldErrors.password ? "field-input-error" : ""}`}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <p id="password-hint" className="mt-1 text-[13px] text-ink-muted">
            At least {MIN_PASSWORD_LENGTH} characters.
          </p>
          {fieldErrors.password && <p className="field-error">{fieldErrors.password}</p>}
        </div>

        {/* §17.1 — terms accepted at registration. Submit stays disabled until then. */}
        <label className="mt-1 flex items-start gap-2.5 text-[14px] leading-relaxed text-ink-soft">
          <input
            type="checkbox"
            className="mt-1 h-4 w-4 accent-accent"
            checked={acceptedTerms}
            onChange={(e) => setAcceptedTerms(e.target.checked)}
          />
          <span>
            I accept the{" "}
            {/* Real links, opening in a new tab so a half-filled form is not lost.
                Until M6 these were bare words naming documents that did not exist,
                which made the checkbox a record of consent to nothing. */}
            <a
              href="/terms"
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent underline"
            >
              Terms of Service
            </a>{" "}
            and{" "}
            <a
              href="/privacy"
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent underline"
            >
              Privacy Policy
            </a>
            , and I understand that WealthPilotX is an educational tool that does not provide
            licensed financial advice.
          </span>
        </label>

        <button type="submit" className="btn-primary mt-2" disabled={submitting || !acceptedTerms}>
          {submitting ? "Creating account…" : "Create account"}
        </button>
      </form>
    </PublicLayout>
  );
}
