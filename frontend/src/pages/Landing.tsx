import { Link } from "react-router-dom";
import { PersistentDisclaimer } from "../components/Disclaimer";

export default function Landing() {
  return (
    <div className="flex min-h-screen flex-col">
      <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center px-6 py-16">
        <p className="font-mono text-xs uppercase tracking-[0.12em] text-accent">WealthPilotX</p>
        <h1 className="mt-4 text-4xl font-semibold leading-tight tracking-tight">
          Understand your risk. Discover opportunities. Build smarter portfolios.
        </h1>
        <p className="mt-5 max-w-prose text-[17px] leading-relaxed text-ink-soft">
          Enter your financial profile once and get a risk classification, a portfolio
          allocation, and plain-language reasoning for both — without needing a finance
          background.
        </p>

        <div className="mt-8 flex gap-3">
          <Link to="/register" className="btn-primary">
            Create an account
          </Link>
          <Link
            to="/login"
            className="rounded border border-line px-4 py-2 text-[15px] font-medium text-ink-soft hover:border-ink-muted"
          >
            Sign in
          </Link>
        </div>
      </main>
      <PersistentDisclaimer />
    </div>
  );
}
