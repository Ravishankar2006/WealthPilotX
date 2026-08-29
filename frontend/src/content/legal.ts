/**
 * The Terms of Service and Privacy Policy referenced at registration (PRD §17.1).
 *
 * Held as data rather than as JSX so the same text can be rendered on a page, and
 * so `LAST_UPDATED` is a single value that cannot drift between the two documents
 * and the consent checkbox.
 *
 * **These are drafted by the project, not by a lawyer.** PRD §17.2 requires legal
 * review before any public launch, and that has not happened. Every section below is
 * written to describe what this software actually does, which is the part an
 * engineer can be accurate about — but accuracy about behaviour is not the same as
 * legal sufficiency, and the documents say so themselves rather than leaving a
 * reader to assume otherwise.
 */

export const LAST_UPDATED = "29 August 2026";

export interface LegalSection {
  heading: string;
  paragraphs: string[];
  list?: string[];
}

export interface LegalDocument {
  title: string;
  summary: string;
  sections: LegalSection[];
}

const NOT_REVIEWED =
  "This document was written by the people who built WealthPilotX, and has not been " +
  "reviewed by a lawyer. It describes what the software does. It is not a substitute " +
  "for legal advice, and it should be reviewed by a qualified professional before " +
  "this project is offered to the public.";

export const TERMS: LegalDocument = {
  title: "Terms of Service",
  summary:
    "WealthPilotX is an educational and research tool. It does not give financial advice, " +
    "does not execute trades, and does not hold your money.",
  sections: [
    {
      heading: "What this service is",
      paragraphs: [
        "WealthPilotX analyses a financial profile you enter yourself, alongside public market " +
          "and economic data, and produces an illustrative portfolio allocation with an " +
          "explanation of how it was derived. It exists to demonstrate and study how such a " +
          "system can be built and evaluated.",
        "It is a research and educational project. It is not a licensed financial, investment, " +
          "tax or legal advisory service, and nothing it produces is a recommendation to buy or " +
          "sell any security.",
      ],
    },
    {
      heading: "What this service will never do",
      paragraphs: [
        "These are permanent limits on the product, not features that are merely absent today:",
      ],
      list: [
        "Execute a trade or route an order.",
        "Connect to a bank or brokerage account.",
        "Hold, transmit or take custody of money or any other asset.",
        "Provide advice that is presented as licensed financial, investment, tax or legal advice.",
        "Guarantee a return, or present a model output as a certain outcome.",
      ],
    },
    {
      heading: "Model outputs and their limits",
      paragraphs: [
        "Risk classifications, market predictions, asset scores, portfolio weights and backtests " +
          "are outputs of statistical models trained on historical data. Past performance does " +
          "not guarantee future results, and a backtest is a simulation over prices that have " +
          "already happened — no money was invested and no returns were realised.",
        "The models, the scoring rules and the constraint bands encode judgments made by the " +
          "people who built this project. No qualified financial professional has reviewed them. " +
          "Every model output carries the version of the model that produced it so that any " +
          "result can be traced and checked.",
        "Market and economic data comes from third parties and may be delayed, incomplete or " +
          "wrong. The service reports when a figure is unavailable rather than substituting an " +
          "estimate, but it cannot detect every error in data it did not produce.",
      ],
    },
    {
      heading: "Your account",
      paragraphs: [
        "You are responsible for the accuracy of the financial information you enter and for " +
          "keeping your password secure. You may delete your account at any time from the Data " +
          "and privacy page; deletion is immediate and cannot be undone.",
        "Because this is a research project rather than a commercial service, it is offered " +
          "as-is and without warranty, availability commitment or support obligation. It may " +
          "change or stop working at any time.",
      ],
    },
    {
      heading: "Decisions are yours",
      paragraphs: [
        "Any decision you make about your own money is yours alone. If you are deciding what to " +
          "do with real savings, consult a licensed financial adviser who can consider your full " +
          "circumstances. This software cannot, and does not try to.",
      ],
    },
    { heading: "Status of this document", paragraphs: [NOT_REVIEWED] },
  ],
};

export const PRIVACY: LegalDocument = {
  title: "Privacy Policy",
  summary:
    "What WealthPilotX stores about you, what it does with it, and how to get it back or " +
    "remove it.",
  sections: [
    {
      heading: "What is collected",
      paragraphs: ["Only what you enter and what the service derives from it:"],
      list: [
        "Your email address and a hashed password. The password itself is never stored and " +
          "cannot be recovered from what is.",
        "The financial profile you submit: age, annual income, savings, risk appetite, " +
          "investment goal, investment horizon, investment experience and financial literacy.",
        "Results derived from that profile: risk assessments, generated portfolios and the " +
          "recommendations behind them.",
      ],
    },
    {
      heading: "How it is protected",
      paragraphs: [
        "Income and savings are encrypted before they reach the database, by the application " +
          "itself, so the protection travels with the data rather than depending on how the " +
          "database happens to be hosted.",
        "Financial values are stripped from application logs by a filter that redacts them by " +
          "field name and by pattern, so that a value cannot reach a log even through a message " +
          "someone wrote carelessly. They are never included in error messages returned to any " +
          "client.",
        "Your data is visible only to you. Another account asking for it receives the same " +
          "response as if it did not exist, because confirming that someone else's record exists " +
          "is itself a disclosure.",
      ],
    },
    {
      heading: "What it is used for",
      paragraphs: [
        "Your profile is used to produce your own risk classification and portfolio, and for " +
          "nothing else. It is not sold, not shared with third parties, and not used to train " +
          "the models — the risk model is trained on a synthetic population generated from a " +
          "documented scoring rubric, not on real users.",
        "The fairness report aggregates outcomes across groups such as age band and income band " +
          "so that the system can be audited for uneven treatment. It reports a group only when " +
          "that group contains at least 20 users, and withholds it entirely below that " +
          "threshold — on a small population, a group statistic describes identifiable people. " +
          "No individual value appears in that report at any group size.",
      ],
    },
    {
      heading: "Your rights over it",
      paragraphs: [
        "From the Data and privacy page you can:",
      ],
      list: [
        "Export your financial profile as a JSON file, at any time.",
        "Delete your account. This removes the account, the financial profile and every active " +
          "session immediately. It is not a soft delete and it cannot be reversed.",
      ],
    },
    {
      heading: "How long it is kept",
      paragraphs: [
        "Your profile and the results derived from it are kept until you delete your account, " +
          "at which point they are removed immediately rather than within the 30 days the " +
          "project committed to.",
        "Market and economic data is public information about securities and economies, not " +
          "about you, and is retained indefinitely.",
      ],
    },
    {
      heading: "Third-party data",
      paragraphs: [
        "Market prices come from Yahoo Finance through an unofficial interface and economic " +
          "series come from FRED. Your personal data is never sent to either — the flow is " +
          "inward only. Because the market data source is unofficial, this project is scoped as " +
          "non-commercial research use.",
      ],
    },
    { heading: "Status of this document", paragraphs: [NOT_REVIEWED] },
  ],
};
