import type { ReactNode } from "react";

/**
 * One labelled number, with a first-class unavailable state.
 *
 * FR-09 requires metrics to be "returned or explicitly marked unavailable with a
 * reason", and the UI honours that rather than rendering a dash that reads as zero
 * (Phase 5 plan, decision 4). A portfolio dashboard showing "0.0%" where it means
 * "we could not compute this" is telling the user something false.
 */

export function Metric({
  label,
  value,
  hint,
  unavailableReason,
  emphasis = false,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  unavailableReason?: string;
  emphasis?: boolean;
}) {
  const unavailable = Boolean(unavailableReason);

  return (
    <div className="min-w-0">
      <dt className="text-[12px] uppercase tracking-wide text-ink-muted">{label}</dt>
      <dd
        className={
          unavailable
            ? "mt-1 text-[14px] italic text-ink-muted"
            : `mt-1 font-mono tabular-nums ${emphasis ? "text-[24px]" : "text-[17px]"}`
        }
      >
        {unavailable ? "Not available" : value}
      </dd>
      {(hint || unavailableReason) && (
        <p className="mt-1 text-[12px] leading-snug text-ink-muted">{unavailableReason ?? hint}</p>
      )}
    </div>
  );
}
