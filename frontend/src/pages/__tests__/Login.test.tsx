import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import Login from "../Login";
import { AuthProvider } from "../../context/AuthContext";

function renderPage() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider>
        <Login />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("Login", () => {
  it("renders the form", () => {
    renderPage();
    expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
  });

  it("shows the server's message when credentials are rejected", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "invalid_credentials",
            message: "Email or password is incorrect.",
            fields: null,
          },
        }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      ),
    );

    renderPage();
    await user.type(screen.getByLabelText(/email/i), "investor@example.com");
    await user.type(screen.getByLabelText(/password/i), "wrong-password");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/incorrect/i);
  });

  it("stores tokens and does not echo the password on success", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          access_token: "access.token.value",
          refresh_token: "refresh-token-value",
          token_type: "bearer",
          expires_at: new Date().toISOString(),
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    renderPage();
    await user.type(screen.getByLabelText(/email/i), "investor@example.com");
    await user.type(screen.getByLabelText(/password/i), "correct-password");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await vi.waitFor(() => {
      expect(localStorage.getItem("wpx.access_token")).toBe("access.token.value");
    });
    expect(localStorage.getItem("wpx.refresh_token")).toBe("refresh-token-value");
  });
});
