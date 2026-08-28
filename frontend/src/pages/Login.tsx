import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PublicLayout } from "../components/Layout";
import { useAuth } from "../context/AuthContext";
import { ApiError } from "../api/types";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not reach the server. Try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <PublicLayout>
      <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
      <p className="mt-2 text-[15px] text-ink-muted">
        New here?{" "}
        <Link to="/register" className="text-accent-dark underline underline-offset-2">
          Create an account
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
            className="field-input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>

        <div>
          <label htmlFor="password" className="field-label">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            className="field-input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        <button type="submit" className="btn-primary mt-2" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </PublicLayout>
  );
}
