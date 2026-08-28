import type { TrendDirection } from "../api/types";
import { signedPercent } from "../lib/format";

/**
 * A predicted direction with its magnitude (FR-08, §16.5).
 *
 * The arrow and the word both carry the direction, so removing colour loses
 * nothing. FLAT is a real answer the model can give — the predictor uses a ±1%
 * dead band precisely so a near-zero prediction is not dressed up as a direction —
 * and it is rendered as such rather than as a weak "up".
 */

const STYLES: Record<TrendDirection, { glyph: string; classes: string; word: string }> = {
  UP: { glyph: "▲", classes: "text-[#14513a]", word: "Upward" },
  DOWN: { glyph: "▼", classes: "text-[#7d281b]", word: "Downward" },
  FLAT: { glyph: "▬", classes: "text-ink-muted", word: "Flat" },
};

export function Trend({
  direction,
  value,
  horizonDays,
}: {
  direction: TrendDirection;
  value?: string | number | null;
  horizonDays?: number;
}) {
  const style = STYLES[direction];
  const horizon = horizonDays ? ` over ${horizonDays} trading days` : "";

  return (
    <span className={`inline-flex items-baseline gap-1.5 font-medium ${style.classes}`}>
      <span aria-hidden="true">{style.glyph}</span>
      <span>{style.word}</span>
      {value !== undefined && value !== null && (
        <span className="font-mono text-[13px]">{signedPercent(value)}</span>
      )}
      <span className="sr-only">
        predicted{horizon}. This is a model estimate, not a forecast.
      </span>
    </span>
  );
}
