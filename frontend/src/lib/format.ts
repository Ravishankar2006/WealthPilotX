/**
 * Every user-visible number goes through here (Phase 5 plan, decision 5).
 *
 * Formatting scattered across a dozen components is how a dashboard ends up
 * showing 0.0642 in one panel and 6.42% in another, and how a percentage that is
 * already a percentage gets multiplied by 100 a second time. One module, one set of
 * conventions.
 *
 * The API sends decimals as strings (Pydantic serialises `Decimal` that way, which
 * is deliberate — it avoids the float rounding a JSON number would introduce), so
 * every function here accepts `string | number`.
 */

type Numeric = string | number | null | undefined;

/** Parse an API decimal. Returns null rather than NaN so callers must handle it. */
export function toNumber(value: Numeric): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** A fraction (0.0642) as a percentage ("6.4%"). */
export function percent(value: Numeric, digits = 1): string {
  const parsed = toNumber(value);
  if (parsed === null) return "—";
  return `${(parsed * 100).toFixed(digits)}%`;
}

/** Same, with an explicit sign — for anything that can be a gain or a loss. */
export function signedPercent(value: Numeric, digits = 1): string {
  const parsed = toNumber(value);
  if (parsed === null) return "—";
  const sign = parsed > 0 ? "+" : "";
  return `${sign}${(parsed * 100).toFixed(digits)}%`;
}

export function decimal(value: Numeric, digits = 2): string {
  const parsed = toNumber(value);
  return parsed === null ? "—" : parsed.toFixed(digits);
}

const CURRENCY = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

export function currency(value: Numeric): string {
  const parsed = toNumber(value);
  return parsed === null ? "—" : CURRENCY.format(parsed);
}

const DATE = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
});

export function date(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : DATE.format(parsed);
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return `${DATE.format(parsed)}, ${parsed.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

/** SCREAMING_SNAKE enum → "Screaming snake", for anything the API sends as an enum. */
export function humanise(value: string | null | undefined): string {
  if (!value) return "—";
  const words = value.replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Direction of a signed value, as a word rather than a colour.
 *
 * §16.5 requires that information not be conveyed by colour alone, so components
 * ask this for the label and use colour only to reinforce it.
 */
export function direction(value: Numeric): "up" | "down" | "flat" {
  const parsed = toNumber(value);
  if (parsed === null || parsed === 0) return "flat";
  return parsed > 0 ? "up" : "down";
}
