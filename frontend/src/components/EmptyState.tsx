import type { ReactNode } from "react";

/**
 * "Nothing here yet, and here is why" — the pattern for every surface that depends
 * on a step the user has not taken.
 *
 * Nothing expensive runs on mount (Phase 5 plan, decision 2): `/risk/analyze` and
 * `/portfolio/generate` are in the 10 req/min bucket and run models on every call,
 * so a refresh would silently spend that budget and a user would find a portfolio
 * generated in their name that they never asked for. So the empty state names the
 * missing step and offers the action.
 */

export function EmptyState({
  title,
  description,
  action,
  blockedBy,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  /** Why the action cannot be taken yet, when it cannot. */
  blockedBy?: string;
}) {
  return (
    <div className="rounded border border-dashed border-line bg-white/60 p-5">
      <h3 className="text-[15px] font-semibold">{title}</h3>
      <p className="mt-1.5 max-w-prose text-[14px] leading-relaxed text-ink-muted">{description}</p>
      {blockedBy ? (
        <p className="mt-3 text-[13px] text-ink-soft">{blockedBy}</p>
      ) : (
        action && <div className="mt-4">{action}</div>
      )}
    </div>
  );
}
