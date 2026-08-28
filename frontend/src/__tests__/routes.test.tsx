import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes } from "../routes";
import { AuthProvider } from "../context/AuthContext";
import { writeTokens } from "../api/tokens";

const FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true };

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]} future={FUTURE}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("route guards", () => {
  it("sends an unauthenticated visitor from a protected route to login", async () => {
    renderAt("/dashboard");
    expect(await screen.findByRole("heading", { name: /sign in/i })).toBeInTheDocument();
  });

  it("sends an already-authenticated visitor away from the register page", async () => {
    writeTokens({ accessToken: "a", refreshToken: "r" });
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ complete: false, missing_fields: [] }, 200));

    renderAt("/register");
    expect(await screen.findByRole("heading", { name: /dashboard/i })).toBeInTheDocument();
  });

  // Regression: the guard used to redirect on the auth-state *change* caused by
  // registering, which beat Register's own navigate and dropped new users on the
  // dashboard instead of the profile form (PRD §8 journey: register → profile).
  it("continues to onboarding after registering, rather than bouncing to the dashboard", async () => {
    const user = userEvent.setup();

    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse(
          {
            user: { id: "u1", email: "new@example.com", created_at: new Date().toISOString() },
            tokens: {
              access_token: "a",
              refresh_token: "r",
              token_type: "bearer",
              expires_at: new Date().toISOString(),
            },
          },
          201,
        ),
      )
      // Onboarding's initial profile fetch: nothing saved yet.
      .mockResolvedValue(
        jsonResponse({ error: { code: "profile_not_found", message: "none", fields: null } }, 404),
      );

    renderAt("/register");

    await user.type(screen.getByLabelText(/email/i), "new@example.com");
    await user.type(screen.getByLabelText(/password/i), "correct-horse-battery");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /create account/i }));

    expect(
      await screen.findByRole("heading", { name: /your financial profile/i }),
    ).toBeInTheDocument();
  });
});
