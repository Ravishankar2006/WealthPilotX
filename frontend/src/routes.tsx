import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useRef, type ReactElement } from "react";
import { useAuth } from "./context/AuthContext";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Onboarding from "./pages/Onboarding";
import Dashboard from "./pages/Dashboard";
import Settings from "./pages/Settings";

function RequireAuth({ children }: { children: ReactElement }) {
  const { isAuthenticated, isLoading } = useAuth();
  const location = useLocation();

  // Wait for the stored session to be read; otherwise a reload on a protected
  // route flashes the login page before settling.
  if (isLoading) return null;

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return children;
}

function RedirectIfAuthenticated({ children }: { children: ReactElement }) {
  const { isAuthenticated, isLoading } = useAuth();

  // Only the state at mount decides this. Redirecting on a *change* would hijack
  // the navigation login and register perform themselves: signing up flipped this
  // to true, the guard re-rendered first, and the new user landed on the dashboard
  // instead of continuing to the profile form.
  const authenticatedOnMount = useRef<boolean | null>(null);
  if (authenticatedOnMount.current === null && !isLoading) {
    authenticatedOnMount.current = isAuthenticated;
  }

  if (isLoading) return null;
  return authenticatedOnMount.current ? <Navigate to="/dashboard" replace /> : children;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route
        path="/login"
        element={
          <RedirectIfAuthenticated>
            <Login />
          </RedirectIfAuthenticated>
        }
      />
      <Route
        path="/register"
        element={
          <RedirectIfAuthenticated>
            <Register />
          </RedirectIfAuthenticated>
        }
      />
      <Route
        path="/onboarding"
        element={
          <RequireAuth>
            <Onboarding />
          </RequireAuth>
        }
      />
      <Route
        path="/dashboard"
        element={
          <RequireAuth>
            <Dashboard />
          </RequireAuth>
        }
      />
      <Route
        path="/settings"
        element={
          <RequireAuth>
            <Settings />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
