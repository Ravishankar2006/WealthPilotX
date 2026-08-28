/**
 * PRD §17.1 — the disclaimers are a compliance requirement, not decoration.
 *
 * `persistent` renders the always-visible footer notice; `inline` renders the
 * per-view notice that must accompany every recommendation and prediction
 * surface once those arrive in Milestones 4 and 5.
 */

const PERSISTENT_TEXT =
  "WealthPilotX is an educational and research decision-support tool. It does not provide " +
  "licensed financial, investment, tax or legal advice, does not execute trades, and does " +
  "not hold funds.";

const INLINE_TEXT =
  "Model outputs and past performance do not guarantee future results. This is not investment advice.";

export function PersistentDisclaimer() {
  return (
    // Sticky, not merely last in the document: §17.1 requires the disclaimer be
    // persistent and unmissable, and on a long page a footer falls below the fold.
    <footer
      className="sticky bottom-0 z-10 border-t border-line bg-ground/95 px-6 py-4 backdrop-blur"
      role="contentinfo"
    >
      <p className="mx-auto max-w-5xl text-[13px] leading-relaxed text-ink-muted">
        <span className="font-medium text-ink-soft">Important. </span>
        {PERSISTENT_TEXT}
      </p>
    </footer>
  );
}

export function InlineDisclaimer({ children }: { children?: React.ReactNode }) {
  return (
    <p
      className="rounded border-l-2 border-warn bg-amber-50/70 px-3 py-2 text-[13px] leading-relaxed text-ink-soft"
      role="note"
    >
      {children ?? INLINE_TEXT}
    </p>
  );
}
