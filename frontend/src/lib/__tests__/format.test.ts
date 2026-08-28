import { describe, expect, it } from "vitest";
import {
  currency,
  date,
  decimal,
  direction,
  humanise,
  percent,
  signedPercent,
  toNumber,
} from "../format";

describe("toNumber", () => {
  it("parses the decimal strings the API sends", () => {
    // Pydantic serialises Decimal as a string on purpose — a JSON number would
    // reintroduce the float rounding the backend went to trouble to avoid.
    expect(toNumber("0.25000000")).toBe(0.25);
    expect(toNumber(0.25)).toBe(0.25);
  });

  it("returns null rather than NaN for anything unusable", () => {
    // NaN propagates silently through arithmetic and renders as "NaN%"; null
    // forces the caller to decide what an absent value looks like.
    for (const value of [null, undefined, "", "abc"]) {
      expect(toNumber(value)).toBeNull();
    }
  });
});

describe("percent", () => {
  it("scales a fraction to a percentage", () => {
    expect(percent("0.0642")).toBe("6.4%");
    expect(percent(0.1)).toBe("10.0%");
  });

  it("renders an em dash for a missing value, never 0%", () => {
    // "0%" is a measurement; "—" is an absence. Conflating them tells the user
    // something false about their portfolio.
    expect(percent(null)).toBe("—");
    expect(percent(undefined)).toBe("—");
  });

  it("respects the requested precision", () => {
    expect(percent(0.123456, 2)).toBe("12.35%");
  });
});

describe("signedPercent", () => {
  it("marks a gain with an explicit plus", () => {
    expect(signedPercent(0.05)).toBe("+5.0%");
  });

  it("keeps the minus on a loss", () => {
    expect(signedPercent(-0.05)).toBe("-5.0%");
  });

  it("does not sign zero", () => {
    expect(signedPercent(0)).toBe("0.0%");
  });
});

describe("decimal and currency", () => {
  it("formats to a fixed precision", () => {
    expect(decimal("0.4761", 3)).toBe("0.476");
  });

  it("formats currency and handles absence", () => {
    expect(currency(1234.5)).toContain("1,234.50");
    expect(currency(null)).toBe("—");
  });
});

describe("date", () => {
  it("formats an ISO date", () => {
    expect(date("2026-08-28")).toMatch(/2026/);
  });

  it("does not render Invalid Date", () => {
    expect(date("not-a-date")).toBe("—");
    expect(date(null)).toBe("—");
  });
});

describe("humanise", () => {
  it("turns an API enum into prose", () => {
    expect(humanise("WEALTH_CREATION")).toBe("Wealth creation");
    expect(humanise("MEDIUM")).toBe("Medium");
  });
});

describe("direction", () => {
  it("names the direction so colour is never the only signal (§16.5)", () => {
    expect(direction(0.02)).toBe("up");
    expect(direction(-0.02)).toBe("down");
    expect(direction(0)).toBe("flat");
    expect(direction(null)).toBe("flat");
  });
});
