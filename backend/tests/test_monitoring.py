"""§10.5 — drift monitoring.

Two things need proving about a drift monitor, and they pull in opposite
directions: it has to fire on a real shift, and it has to stay quiet on a stable
one. A monitor that only satisfies the first is a monitor whose alerts get ignored
by the second week, which is functionally the same as no monitor.

The third thing, and the one most easily skipped: a check that cannot run must say
so rather than reporting STABLE. "Measured and fine" and "not measured" look
identical on a dashboard unless the code refuses to conflate them.
"""

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.ml import monitoring, registry
from app.ml.prediction import dataset as prediction_dataset
from app.ml.prediction import model as prediction_model
from app.models.asset import Asset
from app.models.enums import AssetClass, AssetType, DriftCheck, DriftVerdict, TrendDirection
from app.models.market_data import MarketData
from app.models.model_monitoring import ModelMonitoring
from app.models.model_record import PREDICTION_MODEL
from app.models.prediction import Prediction

RNG_SEED = 4


def _seed_history(
    db: Session,
    symbol: str,
    *,
    days: int = 900,
    volatility: float = 0.011,
    seed: int = RNG_SEED,
) -> Asset:
    asset = Asset(symbol=symbol, asset_type=AssetType.ETF, asset_class=AssetClass.EQUITY)
    db.add(asset)
    db.commit()
    db.refresh(asset)

    rng = np.random.default_rng(seed)
    start = date(2023, 1, 2)
    price = 250.0
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        price *= float(np.exp(rng.normal(0.0003, volatility)))
        rows.append(
            MarketData(
                asset_id=asset.id,
                date=day,
                open=Decimal(str(round(price, 2))),
                high=Decimal(str(round(price * 1.005, 2))),
                low=Decimal(str(round(price * 0.995, 2))),
                close=Decimal(str(round(price, 2))),
                adj_close=Decimal(str(round(price, 2))),
                volume=2_000_000,
                source="test",
            )
        )
    db.add_all(rows)
    db.commit()
    return asset


@pytest.fixture
def promoted_model(db: Session) -> tuple[Asset, object]:
    asset = _seed_history(db, "SPY")
    data = prediction_dataset.build_training_data(db)
    artifact, metrics = prediction_model.train(data)
    record = registry.register(
        db,
        name=PREDICTION_MODEL,
        payload=artifact,
        metrics=metrics,
        training_start=data.train_start.date(),
        training_end=data.train_end.date(),
    )
    registry.promote(db, PREDICTION_MODEL, record.version, force=True)
    return asset, record


class TestPopulationStabilityIndex:
    def test_a_distribution_compared_with_itself_is_zero(self) -> None:
        sample = np.random.default_rng(1).normal(size=2000)
        assert monitoring.population_stability_index(sample, sample) == pytest.approx(0.0, abs=1e-9)

    def test_a_shifted_distribution_crosses_the_alert_band(self) -> None:
        rng = np.random.default_rng(2)
        reference = rng.normal(0, 1, 4000)
        shifted = rng.normal(1.5, 1, 4000)

        value = monitoring.population_stability_index(reference, shifted)

        assert value > monitoring.PSI_ALERT
        assert monitoring.psi_verdict(value) is DriftVerdict.ALERT

    def test_ordinary_resampling_noise_stays_stable(self) -> None:
        """The half that stops the monitor crying wolf. Two draws from the same
        distribution must not look like drift, or every alert is noise."""
        rng = np.random.default_rng(3)
        assert (
            monitoring.psi_verdict(
                monitoring.population_stability_index(rng.normal(size=4000), rng.normal(size=4000))
            )
            is DriftVerdict.STABLE
        )

    def test_too_few_rows_is_insufficient_data_not_stable(self) -> None:
        small = np.random.default_rng(5).normal(size=monitoring.MIN_SAMPLES - 1)
        value = monitoring.population_stability_index(small, small)

        assert np.isnan(value)
        assert monitoring.psi_verdict(value) is DriftVerdict.INSUFFICIENT_DATA

    def test_a_step_series_has_too_few_distinct_values_to_score(self) -> None:
        """The bug the first real run exposed.

        Macro features are monthly series forward-filled onto trading days:
        `inflation` had 11 distinct values across the training window and 2 across
        the recent 90 days. All the recent mass landed in one or two reference
        deciles and PSI came out above 10 — forty times the alert threshold — with
        nothing having drifted. Three ALERTs on the first run, all artefacts, and
        the same three every night thereafter.
        """
        rng = np.random.default_rng(9)
        reference = rng.choice([3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 4.0, 4.1], 4000)
        recent = rng.choice([4.0, 4.1], 1000)

        value = monitoring.population_stability_index(reference, recent)

        assert np.isnan(value)
        assert monitoring.psi_verdict(value) is DriftVerdict.INSUFFICIENT_DATA

    def test_enough_distinct_values_still_scores(self) -> None:
        """The guard must not silence a continuous feature that genuinely moved."""
        rng = np.random.default_rng(10)
        value = monitoring.population_stability_index(
            rng.normal(0, 1, 4000), rng.normal(2.0, 1, 4000)
        )
        assert monitoring.psi_verdict(value) is DriftVerdict.ALERT

    def test_a_constant_feature_is_insufficient_data(self) -> None:
        """Every quantile edge collapses to one value; there is no distribution to
        compare. `np.histogram` would raise on the duplicate edges."""
        constant = np.full(500, 2.5)
        assert np.isnan(monitoring.population_stability_index(constant, constant))


class TestErrorVerdict:
    @pytest.mark.parametrize(
        ("ratio", "expected"),
        [
            (0.9, DriftVerdict.STABLE),
            (1.19, DriftVerdict.STABLE),
            (1.2, DriftVerdict.WATCH),
            (1.49, DriftVerdict.WATCH),
            (1.5, DriftVerdict.ALERT),
            (4.0, DriftVerdict.ALERT),
        ],
    )
    def test_bands_are_the_ones_declared_in_the_plan(
        self, ratio: float, expected: DriftVerdict
    ) -> None:
        assert monitoring.error_verdict(ratio) is expected


class TestRun:
    def test_no_production_model_is_a_skip_not_an_error(self, db: Session) -> None:
        """A scheduler ticking nightly on a stack where nothing is promoted yet is
        an ordinary state, not an incident."""
        assert monitoring.run(db) == []

    def test_it_writes_one_row_per_feature_plus_one_for_error(
        self, db: Session, promoted_model: tuple[Asset, object]
    ) -> None:
        _, record = promoted_model
        rows = monitoring.run(db)

        features = [r for r in rows if r.check is DriftCheck.FEATURE_STABILITY]
        errors = [r for r in rows if r.check is DriftCheck.PREDICTION_ERROR]

        assert len(features) == len(record.metrics["features"])  # type: ignore[attr-defined]
        assert len(errors) == 1
        assert all(r.model_version == record.version for r in rows)  # type: ignore[attr-defined]

    def test_stable_history_does_not_alert(
        self, db: Session, promoted_model: tuple[Asset, object]
    ) -> None:
        """The reference window and the recent window come from one continuous
        random walk with fixed parameters, so any ALERT here is the monitor
        inventing a shift."""
        rows = monitoring.run(db, as_of=date(2025, 6, 1))
        stability = [r for r in rows if r.check is DriftCheck.FEATURE_STABILITY]

        assert stability
        assert all(r.verdict is not DriftVerdict.ALERT for r in stability)

    def test_a_check_that_cannot_run_is_insufficient_data(
        self, db: Session, promoted_model: tuple[Asset, object]
    ) -> None:
        """No prediction has reached the end of its horizon, so there is no realised
        outcome to score. That must not read as a healthy error ratio."""
        rows = monitoring.run(db)
        error_row = next(r for r in rows if r.check is DriftCheck.PREDICTION_ERROR)

        assert error_row.verdict is DriftVerdict.INSUFFICIENT_DATA
        assert error_row.value is None
        assert error_row.details is not None
        assert "reason" in error_row.details

    def test_an_unscoreable_feature_records_the_ranges_instead(
        self, db: Session, promoted_model: tuple[Asset, object]
    ) -> None:
        """When PSI cannot run, the comparison that *is* meaningful for a level
        series must still reach the operator."""
        rows = monitoring.run(db, as_of=date(2025, 6, 1))
        unscoreable = [
            r
            for r in rows
            if r.check is DriftCheck.FEATURE_STABILITY
            and r.verdict is DriftVerdict.INSUFFICIENT_DATA
        ]

        for row in unscoreable:
            assert row.details is not None
            assert "reason" in row.details
            assert "reference_range" in row.details
            assert "recent_range" in row.details

    def test_rows_persist_for_the_next_run_to_compare_against(
        self, db: Session, promoted_model: tuple[Asset, object]
    ) -> None:
        monitoring.run(db)
        assert len(monitoring.latest(db)) > 0

    def test_a_wrong_prediction_raises_the_error_ratio(
        self, db: Session, promoted_model: tuple[Asset, object]
    ) -> None:
        """Deliberately absurd stored predictions against real realised returns.
        The rolling RMSE should dwarf the training RMSE and trip the alert band."""
        asset, record = promoted_model
        base = date(2024, 6, 3)
        for offset in range(0, 120, 10):
            db.add(
                Prediction(
                    asset_id=asset.id,
                    model_version=record.version,  # type: ignore[attr-defined]
                    prediction_date=base + timedelta(days=offset),
                    predicted_return=Decimal("0.95"),
                    trend=TrendDirection.UP,
                    confidence=Decimal("0.9"),
                    horizon_days=20,
                )
            )
        db.commit()

        observation = monitoring.prediction_error(db, record, as_of=date(2025, 6, 1))  # type: ignore[arg-type]

        assert observation.verdict is DriftVerdict.ALERT
        assert observation.value is not None and observation.value > 1.5
        assert observation.details is not None
        assert observation.details["observations"] > 2

    def test_unrealised_horizons_are_excluded_rather_than_part_scored(
        self, db: Session, promoted_model: tuple[Asset, object]
    ) -> None:
        """Scoring a 20-day forecast against a 2-day move would compare two
        different quantities and report the mismatch as model error."""
        asset, record = promoted_model
        db.add(
            Prediction(
                asset_id=asset.id,
                model_version=record.version,  # type: ignore[attr-defined]
                prediction_date=date(2025, 5, 30),
                predicted_return=Decimal("0.5"),
                trend=TrendDirection.UP,
                confidence=Decimal("0.9"),
                horizon_days=20,
            )
        )
        db.commit()

        observation = monitoring.prediction_error(db, record, as_of=date(2025, 6, 1))  # type: ignore[arg-type]

        assert observation.verdict is DriftVerdict.INSUFFICIENT_DATA


class TestJobCommand:
    def test_monitor_returns_rows_and_never_a_failing_exit(
        self, db: Session, promoted_model: tuple[Asset, object]
    ) -> None:
        """A drift alert is a finding, not a job failure. A non-zero exit would make
        a scheduler retry the check and a CI run fail over data the code did not
        change."""
        from app.jobs import ml as ml_jobs

        rows = ml_jobs.monitor(db)
        assert rows
        assert all(isinstance(row, ModelMonitoring) for row in rows)
