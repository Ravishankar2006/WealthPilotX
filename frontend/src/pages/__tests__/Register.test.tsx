import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import Register from "../Register";
import { AuthProvider } from "../../context/AuthContext";

function renderPage() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Register />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("Register", () => {
  it("renders the form", () => {
    renderPage();
    expect(screen.getByRole("heading", { name: /create your account/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  // §17.1 — terms accepted at registration is a compliance requirement, so the
  // gate is tested rather than assumed.
  it("keeps submit disabled until the terms are accepted", async () => {
    const user = userEvent.setup();
    renderPage();

    const submit = screen.getByRole("button", { name: /create account/i });
    expect(submit).toBeDisabled();

    await user.click(screen.getByRole("checkbox"));
    expect(submit).toBeEnabled();
  });

  it("surfaces field-level errors returned by the API", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "validation_error",
            message: "One or more fields are invalid.",
            fields: { password: ["Password is too repetitive."] },
          },
        }),
        { status: 422, headers: { "Content-Type": "application/json" } },
      ),
    );

    renderPage();
    await user.type(screen.getByLabelText(/email/i), "investor@example.com");
    await user.type(screen.getByLabelText(/password/i), "aaaaaaaaaaaa");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /create account/i }));

    expect(await screen.findByText(/too repetitive/i)).toBeInTheDocument();
  });
});
