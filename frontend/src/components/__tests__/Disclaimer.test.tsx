import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { InlineDisclaimer, PersistentDisclaimer } from "../Disclaimer";
import Landing from "../../pages/Landing";

/** §17.1 — the disclaimers are a compliance requirement, so their presence is
 *  asserted rather than left to review. */
describe("Disclaimers", () => {
  it("states that the tool is not licensed financial advice", () => {
    render(<PersistentDisclaimer />);
    expect(screen.getByRole("contentinfo")).toHaveTextContent(
      /does not provide licensed financial, investment, tax or legal advice/i,
    );
  });

  it("warns that model outputs do not guarantee results", () => {
    render(<InlineDisclaimer />);
    expect(screen.getByRole("note")).toHaveTextContent(/do not guarantee future results/i);
  });

  it("appears on the public landing page", () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Landing />
      </MemoryRouter>,
    );
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
  });
});
