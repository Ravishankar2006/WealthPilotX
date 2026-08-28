import type { RiskCategory } from "../api/types";

/**
 * A risk category, encoded three ways (PRD §16.5).
 *
 * Colour, a text label, and a filled-segment glyph. §16.5 requires that
 * information not be conveyed by colour alone, and the reasons go beyond colour
 * vision: a dashboard screenshot pasted into a monochrome report, or a screen read
 * at a glance in bright sunlight, both lose the colour channel entirely.
 *
 * The palette is checked against WCAG 2.1 AA (4.5:1 for text) on its own
 * background — see the contrast test alongside this file.
 */

const STYLES: Record<RiskCategory, { classes: string; filled: number; label: string }> = {
  // 1/3 segments filled, deep green on a pale wash.
  LOW: { classes: "bg-[#dff0e8] text-[#14513a] border-[#a8cfbc]", filled: 1, label: "Low" },
  // 2/3, amber. #6b4a05 on #fdf0d5 clears AA comfortably; the usual amber-600
  // does not, which is why this is darker than it looks like it should be.
  MEDIUM: { classes: "bg-[#fdf0d5] text-[#6b4a05] border-[#e3c98a]", filled: 2, label: "Medium" },
  HIGH: { classes: "bg-[#fbe4e0] text-[#7d281b] border-[#e0aca3]", filled: 3, label: "High" },
};

export function RiskBadge({
  category,
  size = "md",
}: {
  category: RiskCategory;
  size?: "sm" | "md";
}) {
  const style = STYLES[category];
  const text = size === "sm" ? "text-[12px]" : "text-[13px]";

  return (
    <span
      className={`inline-flex items-center gap-2 rounded border px-2.5 py-1 font-medium ${text} ${style.classes}`}
    >
      {/* The glyph carries the same information as the colour. Hidden from
          assistive tech because the label right next to it already says it. */}
      <span className="flex gap-[2px]" aria-hidden="true">
        {[1, 2, 3].map((segment) => (
          <span
            key={segment}
            className={`block h-3 w-1 rounded-[1px] ${
              segment <= style.filled ? "bg-current" : "bg-current opacity-25"
            }`}
          />
        ))}
      </span>
      {style.label} risk
    </span>
  );
}
