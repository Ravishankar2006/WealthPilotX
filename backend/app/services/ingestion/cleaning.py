"""Data cleaning and the data-quality report (FR-06).

FR-06 requires that processed data have "zero nulls in required model-input columns
and zero duplicate (asset_id, date) rows", and that every batch log "a data-quality
report (row counts, null rate, outlier count) for audit". This module is where both
are decided; the database's unique constraint and CHECK constraints are the backstop
that makes the guarantee hold even if a future caller skips this step.

The distinction that matters (Phase 2 plan, decision 4):

* **Structurally impossible** rows are rejected — a missing price, a non-positive
  price, `high < low`, negative volume. These cannot be true of a real session.
* **Extreme but coherent** rows are flagged and kept. A 22% single-day fall is
  Black Monday, not corruption. Dropping it would teach the M3 model that crashes
  do not happen, which is precisely the error a risk tool must not make.
"""

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.providers.base import PriceBar

# Required model-input columns, in FR-06's phrasing. A null in any of these makes
# the row unusable rather than merely imperfect.
REQUIRED_PRICE_FIELDS = ("open", "high", "low", "close", "adj_close")


@dataclass(slots=True)
class QualityReport:
    """The audit record FR-06 asks for. Serialised to `ingestion_runs.quality`."""

    symbol: str | None = None
    rows_in: int = 0
    rows_out: int = 0
    nulls_rejected: int = 0
    non_positive_rejected: int = 0
    inverted_range_rejected: int = 0
    duplicates_removed: int = 0
    outliers_flagged: int = 0
    outlier_dates: list[str] = field(default_factory=list)

    @property
    def rows_rejected(self) -> int:
        return self.nulls_rejected + self.non_positive_rejected + self.inverted_range_rejected

    @property
    def null_rate(self) -> float:
        """Share of input rows dropped for a missing required value."""
        return self.nulls_rejected / self.rows_in if self.rows_in else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "rows_rejected": self.rows_rejected,
            "nulls_rejected": self.nulls_rejected,
            "non_positive_rejected": self.non_positive_rejected,
            "inverted_range_rejected": self.inverted_range_rejected,
            "duplicates_removed": self.duplicates_removed,
            "null_rate": round(self.null_rate, 6),
            "outliers_flagged": self.outliers_flagged,
            # Capped: a pathological backfill should not write an unbounded array
            # into a JSONB column. The count above stays exact.
            "outlier_dates": self.outlier_dates[:50],
        }

    def merge(self, other: "QualityReport") -> None:
        """Fold a per-symbol report into a run-level total."""
        self.rows_in += other.rows_in
        self.rows_out += other.rows_out
        self.nulls_rejected += other.nulls_rejected
        self.non_positive_rejected += other.non_positive_rejected
        self.inverted_range_rejected += other.inverted_range_rejected
        self.duplicates_removed += other.duplicates_removed
        self.outliers_flagged += other.outliers_flagged
        self.outlier_dates.extend(other.outlier_dates)


def _is_valid(bar: PriceBar, report: QualityReport) -> bool:
    values = [getattr(bar, name, None) for name in REQUIRED_PRICE_FIELDS]

    if any(value is None for value in values):
        report.nulls_rejected += 1
        return False

    prices: list[Decimal] = values  # type: ignore[assignment]

    # NaN survives a `is None` check and compares false against everything, so it
    # would slip past the positivity test below and reach a NUMERIC column.
    if any(not value.is_finite() for value in prices):
        report.nulls_rejected += 1
        return False

    if any(value <= 0 for value in prices):
        report.non_positive_rejected += 1
        return False

    if bar.high < bar.low or bar.volume < 0:
        report.inverted_range_rejected += 1
        return False

    return True


def clean_bars(
    symbol: str, bars: list[PriceBar], *, outlier_threshold: float
) -> tuple[list[PriceBar], QualityReport]:
    """Validate, deduplicate and audit one symbol's bars.

    Returns the rows fit to store, ascending by date, and the report describing what
    happened to the rest.
    """
    report = QualityReport(symbol=symbol, rows_in=len(bars))

    # Deduplicate by date, last occurrence winning. Providers occasionally return a
    # provisional bar and a corrected one for the same session in a single response;
    # the later value is the corrected one.
    by_date: dict[Any, PriceBar] = {}
    for bar in bars:
        if not _is_valid(bar, report):
            continue
        if bar.date in by_date:
            report.duplicates_removed += 1
        by_date[bar.date] = bar

    cleaned = sorted(by_date.values(), key=lambda bar: bar.date)
    report.rows_out = len(cleaned)

    _flag_outliers(cleaned, report, outlier_threshold)
    return cleaned, report


def _flag_outliers(bars: list[PriceBar], report: QualityReport, threshold: float) -> None:
    """Count sessions whose absolute log return exceeds `threshold`.

    Log returns rather than simple returns so a +25% and a −25% move are treated with
    the same severity — simple returns are asymmetric and would under-report crashes,
    which is the wrong bias for a risk tool.
    """
    for previous, current in zip(bars, bars[1:], strict=False):
        if previous.close <= 0 or current.close <= 0:
            continue
        log_return = math.log(float(current.close) / float(previous.close))
        if abs(log_return) > threshold:
            report.outliers_flagged += 1
            report.outlier_dates.append(current.date.isoformat())
