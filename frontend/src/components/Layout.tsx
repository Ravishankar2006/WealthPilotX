import { Link, NavLink, useNavigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "../context/AuthContext";
import { PersistentDisclaimer } from "./Disclaimer";

const NAV = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/onboarding", label: "Financial profile" },
  { to: "/settings", label: "Data & privacy" },
];

export function AppLayout({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  const navigate = useNavigate();

  async function handleSignOut() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-5xl items-center gap-6 px-6 py-3">
          <Link to="/dashboard" className="font-mono text-sm font-medium tracking-wide text-accent">
            WealthPilotX
          </Link>
          <nav className="flex gap-4" aria-label="Main">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `text-sm ${isActive ? "font-medium text-ink" : "text-ink-muted hover:text-ink"}`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <button
            type="button"
            onClick={handleSignOut}
            className="ml-auto text-sm text-ink-muted hover:text-ink"
          >
            Sign out
          </button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-10">{children}</main>

      <PersistentDisclaimer />
    </div>
  );
}

export function PublicLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-6 py-12">
        {children}
      </main>
      <PersistentDisclaimer />
    </div>
  );
}
