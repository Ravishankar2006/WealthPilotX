import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { AppLayout } from "../components/Layout";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { ApiError, type FinancialProfile } from "../api/types";

/** PRD §11.2 — right of access and right to erasure, surfaced to the user. */
export default function Settings() {
  const { forgetSession } = useAuth();
  const navigate = useNavigate();

  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const CONFIRM_PHRASE = "DELETE";

  async function handleExport() {
    setError(null);
    try {
      const profile = await api.get<FinancialProfile>("/user/profile");
      const blob = new Blob([JSON.stringify(profile, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "wealthpilotx-profile.json";
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 404
          ? "There is no profile to export yet."
          : "Could not export your data. Try again.",
      );
    }
  }

  async function handleDelete() {
    setError(null);
    setDeleting(true);
    try {
      await api.delete("/user/profile");
      forgetSession();
      navigate("/", { replace: true });
    } catch {
      setError("Could not delete your account. Try again.");
      setDeleting(false);
    }
  }

  return (
    <AppLayout>
      <h1 className="text-2xl font-semibold tracking-tight">Data & privacy</h1>
      <p className="mt-2 max-w-prose text-[15px] leading-relaxed text-ink-muted">
        You can take a copy of your data or remove it entirely. Deletion is immediate and
        cannot be undone.
      </p>

      {error && (
        <p role="alert" className="mt-6 rounded border-l-2 border-danger bg-red-50 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      <section className="mt-8 rounded border border-line bg-white p-5">
        <h2 className="text-[15px] font-semibold">Export your data</h2>
        <p className="mt-1.5 max-w-prose text-[14px] leading-relaxed text-ink-muted">
          Downloads your financial profile as JSON. Recommendation and risk history will be
          included once those features ship.
        </p>
        <button
          type="button"
          onClick={handleExport}
          className="mt-4 rounded border border-line px-4 py-2 text-[15px] font-medium text-ink-soft hover:border-ink-muted"
        >
          Download my data
        </button>
      </section>

      <section className="mt-5 rounded border border-danger/40 bg-white p-5">
        <h2 className="text-[15px] font-semibold text-danger">Delete your account</h2>
        <p className="mt-1.5 max-w-prose text-[14px] leading-relaxed text-ink-muted">
          Removes your account, your financial profile and every active session. This is a
          permanent deletion, not a deactivation.
        </p>

        <label htmlFor="confirm" className="field-label mt-4">
          Type <span className="font-mono font-semibold">{CONFIRM_PHRASE}</span> to confirm
        </label>
        <input
          id="confirm"
          type="text"
          autoComplete="off"
          className="field-input max-w-xs"
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
        />

        <button
          type="button"
          onClick={handleDelete}
          disabled={confirmText !== CONFIRM_PHRASE || deleting}
          className="btn-danger mt-4 block"
        >
          {deleting ? "Deleting…" : "Delete my account"}
        </button>
      </section>
    </AppLayout>
  );
}
