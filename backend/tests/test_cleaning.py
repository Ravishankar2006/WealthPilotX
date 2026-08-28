"""FR-06 cleaning rules and the data-quality report.

The acceptance criteria are specific: zero nulls in required model-input columns,
zero duplicate (asset_id, date) rows, and a logged report carrying row counts, null
rate and outlier count.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.providers.base import PriceBar
from app.services.ingestion.cleaning import clean_bars

THRESHOLD = 0.25


def bar(day: int, close: str = "100.00", **overrides: object) -> PriceBar:
    values: dict[str, object] = {
        "date": date(2026, 1, 1) + timedelta(days=day),
        "open": Decimal(close),
        "high": Decimal(close) + Decimal("1"),
        "low": Decimal(close) - Decimal("1"),
        "close": Decimal(close),
        "adj_close": Decimal(close),
        "volume": 1_000,
    }
    values.update(overrides)
    return PriceBar(**values)  # type: ignore[arg-type]


class TestStructuralRejects:
    def test_a_row_missing_a_required_price_is_rejected(self) -> None:
        missing_close = PriceBar(
            date=date(2026, 1, 2),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=None,  # type: ignore[arg-type]
            adj_close=Decimal("100"),
            volume=1_000,
        )
        cleaned, report = clean_bars("SPY", [bar(0), missing_close], outlier_threshold=THRESHOLD)
        assert len(cleaned) == 1
        assert report.nulls_rejected == 1
        assert report.null_rate == 0.5

    def test_a_nan_price_is_rejected_rather_than_stored(self) -> None:
        """NaN survives an `is None` check and compares false against everything, so
        it would slip past the positivity test and reach a NUMERIC column."""
        cleaned, report = clean_bars("SPY", [bar(0, close="NaN")], outlier_threshold=THRESHOLD)
        assert cleaned == []
        assert report.nulls_rejected == 1

    def test_non_positive_prices_are_rejected(self) -> None:
        cleaned, report = clean_bars(
            "SPY", [bar(0), bar(1, close="0")], outlier_threshold=THRESHOLD
        )
        assert len(cleaned) == 1
        assert report.non_positive_rejected == 1

    def test_high_below_low_is_rejected(self) -> None:
        cleaned, report = clean_bars(
            "SPY",
            [bar(0, high=Decimal("50"), low=Decimal("120"))],
            outlier_threshold=THRESHOLD,
        )
        assert cleaned == []
        assert report.inverted_range_rejected == 1

    def test_negative_volume_is_rejected(self) -> None:
        cleaned, report = clean_bars("SPY", [bar(0, volume=-5)], outlier_threshold=THRESHOLD)
        assert cleaned == []
        assert report.inverted_range_rejected == 1


class TestDeduplication:
    def test_duplicate_dates_collapse_to_the_last_value(self) -> None:
        """Providers return a provisional bar and its correction in one response;
        the later value is the corrected one."""
        cleaned, report = clean_bars(
            "SPY",
            [bar(0, close="100.00"), bar(0, close="103.00")],
            outlier_threshold=THRESHOLD,
        )
        assert len(cleaned) == 1
        assert cleaned[0].close == Decimal("103.00")
        assert report.duplicates_removed == 1

    def test_output_has_no_duplicate_dates(self) -> None:
        cleaned, _ = clean_bars("SPY", [bar(n % 3) for n in range(12)], outlier_threshold=THRESHOLD)
        assert len({b.date for b in cleaned}) == len(cleaned)

    def test_output_is_sorted_ascending(self) -> None:
        cleaned, _ = clean_bars("SPY", [bar(5), bar(1), bar(3)], outlier_threshold=THRESHOLD)
        assert [b.date for b in cleaned] == sorted(b.date for b in cleaned)


class TestOutliers:
    def test_an_extreme_move_is_flagged_and_kept(self) -> None:
        """Phase 2 plan, decision 4. A 40% fall is Black Monday, not corruption —
        dropping it would teach the M3 model that crashes do not happen."""
        cleaned, report = clean_bars(
            "SPY",
            [bar(0, close="100.00"), bar(1, close="60.00")],
            outlier_threshold=THRESHOLD,
        )
        assert len(cleaned) == 2
        assert report.outliers_flagged == 1
        assert report.outlier_dates == ["2026-01-02"]

    def test_an_ordinary_move_is_not_flagged(self) -> None:
        _, report = clean_bars(
            "SPY",
            [bar(0, close="100.00"), bar(1, close="101.50")],
            outlier_threshold=THRESHOLD,
        )
        assert report.outliers_flagged == 0

    def test_rises_and_falls_are_treated_symmetrically(self) -> None:
        """Log returns, not simple returns: a simple-return threshold under-reports
        falls, which is the wrong bias for a risk tool."""
        _, up = clean_bars(
            "SPY", [bar(0, close="100"), bar(1, close="135")], outlier_threshold=THRESHOLD
        )
        _, down = clean_bars(
            "SPY", [bar(0, close="135"), bar(1, close="100")], outlier_threshold=THRESHOLD
        )
        assert up.outliers_flagged == down.outliers_flagged == 1

    def test_the_stored_date_list_is_capped(self) -> None:
        """A pathological backfill must not write an unbounded array into JSONB."""
        bars = []
        price = 100.0
        for n in range(200):
            price = price * (2.0 if n % 2 == 0 else 0.5)
            bars.append(bar(n, close=f"{price:.2f}"))
        _, report = clean_bars("SPY", bars, outlier_threshold=THRESHOLD)
        assert report.outliers_flagged > 50
        assert len(report.as_dict()["outlier_dates"]) == 50


class TestQualityReport:
    def test_it_carries_everything_fr_06_asks_for(self) -> None:
        _, report = clean_bars("SPY", [bar(0), bar(1)], outlier_threshold=THRESHOLD)
        payload = report.as_dict()
        for key in ("rows_in", "rows_out", "null_rate", "outliers_flagged", "duplicates_removed"):
            assert key in payload

    def test_it_is_json_serialisable_for_the_jsonb_column(self) -> None:
        import json

        _, report = clean_bars("SPY", [bar(0)], outlier_threshold=THRESHOLD)
        assert json.loads(json.dumps(report.as_dict()))["symbol"] == "SPY"

    def test_merging_accumulates_run_level_totals(self) -> None:
        _, first = clean_bars("SPY", [bar(0), bar(0)], outlier_threshold=THRESHOLD)
        _, second = clean_bars("QQQ", [bar(0), bar(1)], outlier_threshold=THRESHOLD)
        first.merge(second)
        assert first.rows_in == 4
        assert first.rows_out == 3
        assert first.duplicates_removed == 1

    def test_an_empty_batch_does_not_divide_by_zero(self) -> None:
        _, report = clean_bars("SPY", [], outlier_threshold=THRESHOLD)
        assert report.null_rate == 0.0
        assert report.rows_out == 0
