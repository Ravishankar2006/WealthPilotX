import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { PrivacyPage, TermsPage } from "../Legal";
import { PRIVACY, TERMS } from "../../content/legal";

/**
 * §17.1's documents.
 *
 * The tests worth writing here are not about layout. They are about the two
 * properties that make the consent checkbox mean anything: the documents exist and
 * say what the product actually does, and they admit what they are not.
 */

function renderPage(element: React.ReactElement) {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      {element}
    </MemoryRouter>,
  );
}

describe("Terms of Service", () => {
  it("renders every section it declares", () => {
    renderPage(<TermsPage />);
    for (const section of TERMS.sections) {
      expect(screen.getByRole("heading", { name: section.heading })).toBeInTheDocument();
    }
  });

  it("states the hard non-goals as permanent, not merely absent", () => {
    // PRD §5. If these ever quietly soften, this fails.
    renderPage(<TermsPage />);
    expect(screen.getByText(/permanent limits on the product/i)).toBeInTheDocument();
    expect(screen.getByText("Execute a trade or route an order.")).toBeInTheDocument();
    expect(
      screen.getByText("Hold, transmit or take custody of money or any other asset."),
    ).toBeInTheDocument();
  });

  it("admits it has not been reviewed by a lawyer", () => {
    // §17.2 requires legal review before any public launch. A terms page that
    // implied it had already happened would be the single most misleading thing in
    // the product.
    renderPage(<TermsPage />);
    expect(screen.getByText(/has not been reviewed by a lawyer/i)).toBeInTheDocument();
  });
});

describe("Privacy Policy", () => {
  it("renders every section it declares", () => {
    renderPage(<PrivacyPage />);
    for (const section of PRIVACY.sections) {
      expect(screen.getByRole("heading", { name: section.heading })).toBeInTheDocument();
    }
  });

  it("describes the §11.2 commitments the system actually implements", () => {
    renderPage(<PrivacyPage />);
    // Encryption at rest, the erasure right, and the fairness suppression threshold
    // are all things the code does — so the policy can be checked against it.
    expect(screen.getByText(/encrypted before they reach the database/i)).toBeInTheDocument();
    expect(screen.getByText(/Export your financial profile as a JSON file/i)).toBeInTheDocument();
    expect(screen.getByText(/at least 20 users/i)).toBeInTheDocument();
  });

  it("says the profile is not used to train the models", () => {
    renderPage(<PrivacyPage />);
    expect(screen.getByText(/not used to train the models/i)).toBeInTheDocument();
  });

  it("links to the other document and back to registration", () => {
    renderPage(<PrivacyPage />);
    expect(screen.getByRole("link", { name: "Terms of Service" })).toHaveAttribute(
      "href",
      "/terms",
    );
    expect(screen.getByRole("link", { name: /back to registration/i })).toHaveAttribute(
      "href",
      "/register",
    );
  });
});
