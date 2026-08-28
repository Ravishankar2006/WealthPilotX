import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { AppRoutes } from "./routes";
import "./index.css";

const container = document.getElementById("root");
if (!container) throw new Error("Root element not found.");

createRoot(container).render(
  <StrictMode>
    {/* Opt in to the v7 behaviours now, so the upgrade is not a behaviour change. */}
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
