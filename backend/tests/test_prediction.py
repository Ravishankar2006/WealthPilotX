"""FR-08 and FR-09 — the market predictor, its split discipline, and its endpoint.

The split tests are the ones that make the reported metrics mean anything. §18 asks
for the split methodology and leakage checks to be published; these are the checks.
"""

import dataclasses
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ml.features.market import FEATURE_COLUMNS, PREDICTION_HORIZON_DAYS, TARGET_COLUMN
from app.ml.prediction import dataset as prediction_dataset
from app.ml.prediction import model as prediction_model
from app.models.asset import Asset
from app.models.enums import AssetClass, AssetType, TrendDirection
from app.models.market_data import MarketData
from app.schemas.risk import MODEL_OUTPUT_DISCLAIMER


def _field_names(artifact: object) -> set[str]:
    """Field names of a slots dataclass. `vars()` raises on these — slots means no
    __dict__ — and that is exactly why the artifacts use them."""
    return {field.name for field in dataclasses.fields(artifact)}  # type: ignore[arg-type]


@pytest.fixture
def asset_with_history(db: Session) -> Asset:
    """One asset with three years of deterministic daily bars."""
    asset = Asset(
        symbol="SPY",
        name="SPDR S&P 500 ETF Trust",
        asset_type=AssetType.ETF,
        asset_class=AssetClass.EQUITY,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    rng = np.random.default_rng(11)
    start = date(2023, 1, 2)
    price = 400.0
    rows = []
    for offset in range(750):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        price *= float(np.exp(rng.normal(0.0004, 0.011)))
        rows.append(
            MarketData(
                asset_id=asset.id,
                date=day,
                open=Decimal(str(round(price, 2))),
                high=Decimal(str(round(price * 1.005, 2))),
                low=Decimal(str(round(price * 0.995, 2))),
                close=Decimal(str(round(price, 2))),
                adj_close=Decimal(str(round(price, 2))),
                volume=1_000_000,
                source="test",
            )
        )
    db.add_all(rows)
    db.commit()
    return asset


class TestSplitDiscipline:
    def test_test_data_comes_strictly_after_training_data(
        self, db: Session, asset_with_history: Asset
    ) -> None:
        data = prediction_dataset.build_training_data(db)
        assert not data.is_empty
        assert data.train_end < data.test_start

    def test_the_purge_gap_is_at_least_one_horizon(
        self, db: Session, asset_with_history: Asset
    ) -> None:
        """Even a chronological split leaks at the seam: the last training row's
        target reaches 20 days forward, into the test period."""
        data = prediction_dataset.build_training_data(db)
        gap = (data.test_start - data.train_end).days
        assert gap >= PREDICTION_HORIZON_DAYS, f"purge gap was only {gap} days"

    def test_splitting_is_by_date_not_by_row_index(self) -> None:
        """A pooled matrix has many assets on the same day; an index split would cut
        through the middle of a date and put one asset's day on each side."""
        dates = pd.bdate_range("2024-01-01", periods=300)
        frame = pd.concat(
            [
                pd.DataFrame(
                    {
                        **{
                            column: np.random.default_rng(i).normal(size=300)
                            for column in FEATURE_COLUMNS
                        },
                        TARGET_COLUMN: np.random.default_rng(i).normal(size=300),
                        "date": dates,
                        "symbol": symbol,
                    }
                )
                for i, symbol in enumerate(["AAA", "BBB", "CCC"])
            ],
            ignore_index=True,
        )

        data = prediction_dataset.chronological_split(frame)
        assert data.train_end < data.test_start

    def test_an_empty_matrix_does_not_raise(self, db: Session) -> None:
        data = prediction_dataset.build_training_data(db)
        assert data.is_empty

    def test_assets_with_too_little_history_are_excluded(self, db: Session) -> None:
        """A newly tracked symbol has no usable rows; including it would contribute
        only warm-up NaNs."""
        asset = Asset(symbol="NEW", asset_type=AssetType.ETF, asset_class=AssetClass.EQUITY)
        db.add(asset)
        db.commit()
        db.add(
            MarketData(
                asset_id=asset.id,
                date=date(2026, 1, 5),
                open=Decimal("10"),
                high=Decimal("11"),
                low=Decimal("9"),
                close=Decimal("10"),
                adj_close=Decimal("10"),
                volume=1,
                source="test",
            )
        )
        db.commit()

        assert prediction_dataset.build_pooled_matrix(db).empty


class TestModel:
    @pytest.fixture
    def trained(self, db: Session, asset_with_history: Asset):
        data = prediction_dataset.build_training_data(db)
        return prediction_model.train(data)

    def test_it_reports_the_metrics_section_18_requires(self, trained) -> None:
        _, metrics = trained
        for key in ("mae", "rmse", "r2"):
            assert key in metrics
            assert np.isfinite(metrics[key])

    def test_it_publishes_its_split_methodology(self, trained) -> None:
        """§18: the split methodology and leakage checks must be published, not
        merely performed."""
        _, metrics = trained
        assert "purge" in metrics["split_method"]
        assert metrics["train_range"] and metrics["test_range"]

    def test_it_records_whether_it_beat_the_naive_baseline(self, trained) -> None:
        """§10.5's first-promotion gate. On synthetic random-walk data the honest
        answer may well be no — which is the correct outcome, not a failure."""
        _, metrics = trained
        assert isinstance(metrics["beats_baseline"], bool)
        assert "baseline_rmse" in metrics

    def test_a_prediction_has_the_right_shape_and_ranges(self, trained) -> None:
        artifact, _ = trained
        features = pd.DataFrame([{column: 0.0 for column in FEATURE_COLUMNS}])
        result = prediction_model.predict(artifact, features)

        assert isinstance(result.trend, TrendDirection)
        assert 0.0 <= result.confidence <= 1.0
        assert np.isfinite(result.predicted_return)
        assert result.horizon_days == PREDICTION_HORIZON_DAYS

    def test_training_with_no_data_gives_an_actionable_error(self, db: Session) -> None:
        data = prediction_dataset.build_training_data(db)
        with pytest.raises(ValueError, match="ingest-market"):
            prediction_model.train(data)

    def test_the_artifact_carries_no_training_rows(self, trained) -> None:
        artifact, _ = trained
        assert _field_names(artifact) == {
            "model",
            "lower",
            "upper",
            "feature_columns",
            "horizon_days",
        }


class TestTrendAndConfidence:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.05, TrendDirection.UP),
            (0.011, TrendDirection.UP),
            (0.005, TrendDirection.FLAT),
            (0.0, TrendDirection.FLAT),
            (-0.005, TrendDirection.FLAT),
            (-0.02, TrendDirection.DOWN),
        ],
    )
    def test_the_dead_band_lets_the_model_answer_flat(
        self, value: float, expected: TrendDirection
    ) -> None:
        """Without it, a predicted +0.02% is reported as 'UP' in the same vocabulary
        as a predicted 8% rally."""
        assert prediction_model._trend(value) is expected

    def test_a_narrow_interval_means_higher_confidence(self) -> None:
        assert prediction_model._confidence(0.01) > prediction_model._confidence(0.15)

    def test_confidence_stays_within_bounds(self) -> None:
        for spread in (-1.0, 0.0, 0.05, 0.5, float("inf"), float("nan")):
            assert 0.0 <= prediction_model._confidence(spread) <= 1.0


class TestPredictionEndpoint:
    def test_an_asset_with_no_prediction_is_a_404(
        self, client: TestClient, auth_headers: dict[str, str], asset_with_history: Asset
    ) -> None:
        """The asset exists; this result does not. That is a 404, not a 500."""
        response = client.get("/api/v1/market/SPY/prediction", headers=auth_headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "no_prediction"

    def test_an_unknown_symbol_is_a_404(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get("/api/v1/market/NOTREAL/prediction", headers=auth_headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "asset_not_found"

    def test_it_requires_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/market/SPY/prediction").status_code == 401

    def test_it_returns_the_full_fr_09_payload(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db: Session,
        asset_with_history: Asset,
    ) -> None:
        from app.models.prediction import Prediction

        db.add(
            Prediction(
                asset_id=asset_with_history.id,
                model_version="v1",
                prediction_date=date(2026, 1, 5),
                predicted_return=Decimal("0.0234"),
                trend=TrendDirection.UP,
                confidence=Decimal("0.72"),
                horizon_days=20,
            )
        )
        db.commit()

        body = client.get("/api/v1/market/SPY/prediction", headers=auth_headers).json()

        # FR-08
        assert body["trend"] == "UP"
        assert body["model_version"] == "v1"
        assert Decimal(str(body["confidence"])) == Decimal("0.7200")
        # FR-09's six metrics, each present or explicitly unavailable
        assert body["expected_return"] is not None
        assert body["volatility"] is not None
        assert body["momentum"] is not None
        assert body["risk_score"] is not None
        # §17.1
        assert body["disclaimer"] == MODEL_OUTPUT_DISCLAIMER

    def test_unavailable_metrics_are_named_rather_than_imputed(
        self, client: TestClient, auth_headers: dict[str, str], db: Session
    ) -> None:
        """FR-09: "returned or explicitly marked unavailable with a reason"."""
        from app.models.prediction import Prediction

        asset = Asset(symbol="NEW", asset_type=AssetType.ETF, asset_class=AssetClass.EQUITY)
        db.add(asset)
        db.commit()
        db.refresh(asset)
        db.add(
            Prediction(
                asset_id=asset.id,
                model_version="v1",
                prediction_date=date(2026, 1, 5),
                predicted_return=Decimal("0.01"),
                trend=TrendDirection.FLAT,
                confidence=Decimal("0.5"),
                horizon_days=20,
            )
        )
        db.commit()

        body = client.get("/api/v1/market/NEW/prediction", headers=auth_headers).json()
        assert set(body["unavailable"]) == {"volatility", "momentum", "risk_score"}
        assert body["volatility"] is None
