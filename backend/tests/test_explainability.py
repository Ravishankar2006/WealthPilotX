"""FR-13 (advanced) — TreeSHAP attributions for market predictions.

The load-bearing test in this file is the reconciliation one. A feature-importance
panel that does not add up to the prediction is a decorative bar chart, and it is
indistinguishable from a correct one by eye — which is exactly why it needs an
assertion rather than a screenshot.
"""

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ml import explain, registry
from app.ml.features import market as market_features
from app.ml.prediction import dataset as prediction_dataset
from app.ml.prediction import model as prediction_model
from app.models.asset import Asset
from app.models.enums import AssetClass, AssetType
from app.models.market_data import MarketData
from app.models.model_record import PREDICTION_MODEL
from app.models.prediction import Prediction
from app.schemas.risk import MODEL_OUTPUT_DISCLAIMER


def _seed_history(db: Session, symbol: str, *, days: int = 750, seed: int = 7) -> Asset:
    asset = Asset(symbol=symbol, asset_type=AssetType.ETF, asset_class=AssetClass.EQUITY)
    db.add(asset)
    db.commit()
    db.refresh(asset)

    rng = np.random.default_rng(seed)
    start = date(2023, 1, 2)
    price = 300.0
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        price *= float(np.exp(rng.normal(0.0004, 0.011)))
        rows.append(
            MarketData(
                asset_id=asset.id,
                date=day,
                open=Decimal(str(round(price, 2))),
                high=Decimal(str(round(price * 1.006, 2))),
                low=Decimal(str(round(price * 0.994, 2))),
                close=Decimal(str(round(price, 2))),
                adj_close=Decimal(str(round(price, 2))),
                volume=1_500_000,
                source="test",
            )
        )
    db.add_all(rows)
    db.commit()
    return asset


@pytest.fixture
def served_prediction(db: Session) -> tuple[Asset, str]:
    """An asset with a stored prediction made by a registered, promoted model.

    Built through the real path — train, register, promote, generate — rather than
    by inserting a Prediction row with a made-up version. The explanation endpoint's
    whole contract is that it loads *the model that made this row*, so a fixture
    that fakes the version would test nothing.
    """
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

    from app.services import prediction_service

    prediction = prediction_service.generate_for_asset(db, asset, artifact, record.version)
    assert prediction is not None
    db.add(prediction)
    db.commit()

    return asset, record.version


class TestTreeShapAttribution:
    def test_contributions_and_base_value_sum_to_the_prediction(
        self, db: Session, served_prediction: tuple[Asset, str]
    ) -> None:
        """The Shapley identity. Without it these numbers explain nothing."""
        asset, version = served_prediction
        loaded = registry.resolve_production(db, PREDICTION_MODEL)
        built = market_features.build_inference_row(
            db, asset.symbol, feature_columns=loaded.payload.feature_columns
        )
        assert built is not None

        attribution = explain.explain(loaded.payload, built[0])

        assert explain.contributions_reconcile(attribution)
        assert abs(attribution.residual) <= explain.RECONCILE_TOLERANCE

    def test_every_fitted_feature_gets_a_contribution(
        self, db: Session, served_prediction: tuple[Asset, str]
    ) -> None:
        asset, _ = served_prediction
        loaded = registry.resolve_production(db, PREDICTION_MODEL)
        built = market_features.build_inference_row(
            db, asset.symbol, feature_columns=loaded.payload.feature_columns
        )
        assert built is not None

        attribution = explain.explain(loaded.payload, built[0])

        assert tuple(c.feature for c in attribution.contributions) == loaded.payload.feature_columns

    def test_top_ranks_by_magnitude_and_keeps_both_signs(self) -> None:
        """A ranking that dropped negative contributions would show only the
        bullish half of the explanation and read as an unqualified buy case."""
        attribution = explain.Attribution(
            base_value=0.0,
            predicted_return=0.0,
            contributions=(
                explain.Contribution("a", "A", 1.0, 0.01),
                explain.Contribution("b", "B", 1.0, -0.05),
                explain.Contribution("c", "C", 1.0, 0.002),
            ),
        )

        top = attribution.top(2)
        assert [c.feature for c in top] == ["b", "a"]
        assert top[0].direction == "decreases"

    def test_it_refuses_more_than_one_row(self, db: Session) -> None:
        """Silently explaining row 0 of a batch is the kind of bug that produces a
        confident, wrong panel rather than an error."""
        asset = _seed_history(db, "QQQ")
        data = prediction_dataset.build_training_data(db)
        artifact, _ = prediction_model.train(data)

        with pytest.raises(ValueError, match="exactly one feature row"):
            explain.explain(artifact, data.x_train.head(2))

        assert asset.symbol == "QQQ"

    def test_unmapped_features_fall_back_to_their_identifier(self) -> None:
        assert explain.label_for("rsi_14") == "Relative strength (14-day)"
        assert explain.label_for("some_new_feature") == "some new feature"


class TestExplanationEndpoint:
    def test_it_requires_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/market/SPY/prediction/explanation").status_code == 401

    def test_it_is_not_shadowed_by_the_history_route(
        self, client: TestClient, auth_headers: dict[str, str], db: Session
    ) -> None:
        """`/{symbol}` is declared in the same router and would match
        `SPY/prediction/explanation` as a symbol if the order were wrong."""
        response = client.get("/api/v1/market/NOPE/prediction/explanation", headers=auth_headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "asset_not_found"

    def test_an_asset_with_no_prediction_is_a_404_naming_the_reason(
        self, client: TestClient, auth_headers: dict[str, str], db: Session
    ) -> None:
        _seed_history(db, "AGG", days=300)
        response = client.get("/api/v1/market/AGG/prediction/explanation", headers=auth_headers)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "no_prediction"
        assert "no production prediction model" in response.json()["error"]["message"]

    def test_it_returns_a_reconciling_decomposition(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db: Session,
        served_prediction: tuple[Asset, str],
    ) -> None:
        asset, version = served_prediction
        response = client.get(
            f"/api/v1/market/{asset.symbol}/prediction/explanation", headers=auth_headers
        )
        assert response.status_code == 200, response.text
        body = response.json()

        # §10.5 — the served result names the model that produced it.
        assert body["model_version"] == version
        assert body["reproduced"] is True
        # §17.1 on a prediction surface.
        assert body["disclaimer"] == MODEL_OUTPUT_DISCLAIMER

        assert body["contributions_shown"] == len(body["contributions"])
        assert body["contributions_shown"] <= body["contributions_total"]
        assert body["contributions_total"] == len(
            registry.resolve_production(db, PREDICTION_MODEL).payload.feature_columns
        )

        for item in body["contributions"]:
            assert item["direction"] in {"increases", "decreases"}
            assert item["label"]

    def test_it_explains_the_version_that_made_the_prediction_not_the_current_one(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db: Session,
        served_prediction: tuple[Asset, str],
    ) -> None:
        """A second model promoted afterwards must not silently take credit."""
        asset, first_version = served_prediction

        data = prediction_dataset.build_training_data(db)
        artifact, metrics = prediction_model.train(data)
        second = registry.register(
            db,
            name=PREDICTION_MODEL,
            payload=artifact,
            metrics=metrics,
            training_start=data.train_start.date(),
            training_end=data.train_end.date(),
        )
        registry.promote(db, PREDICTION_MODEL, second.version, force=True)
        assert second.version != first_version

        body = client.get(
            f"/api/v1/market/{asset.symbol}/prediction/explanation", headers=auth_headers
        ).json()

        assert body["model_version"] == first_version

    def test_a_missing_artifact_is_a_503_not_a_500(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db: Session,
        served_prediction: tuple[Asset, str],
    ) -> None:
        """An artifact lost from the volume is an operational state. It should not
        look like a bug in the request."""
        asset, version = served_prediction
        record = registry.get_record(db, PREDICTION_MODEL, version)
        from pathlib import Path

        Path(record.artifact_path).unlink()

        response = client.get(
            f"/api/v1/market/{asset.symbol}/prediction/explanation", headers=auth_headers
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "explanation_unavailable"

    def test_an_unregistered_version_is_a_503(
        self, db: Session, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        asset = _seed_history(db, "GLD", days=400)
        db.add(
            Prediction(
                asset_id=asset.id,
                model_version="v99",
                prediction_date=date(2024, 6, 3),
                predicted_return=Decimal("0.01"),
                trend="FLAT",
                confidence=Decimal("0.5"),
                horizon_days=20,
            )
        )
        db.commit()

        response = client.get("/api/v1/market/GLD/prediction/explanation", headers=auth_headers)
        assert response.status_code == 503
        assert "no longer registered" in response.json()["error"]["message"]


class TestRubricAlignment:
    """M6 model validation: does the forest use the factors the rubric declares?"""

    def test_it_reports_shares_against_the_declared_weights(self) -> None:
        from app.ml.risk import model as risk_model
        from app.ml.risk import rubric

        artifact, metrics = risk_model.train(population=1500)
        alignment = metrics["rubric_alignment"]

        assert set(alignment["declared_weight"]) == set(rubric.declared_weights())
        assert set(alignment["importance_share"]) == set(rubric.declared_weights())
        assert alignment["importance_share"].keys() == alignment["declared_weight"].keys()
        # Shares are a normalised split of the measured importance.
        assert abs(sum(alignment["importance_share"].values()) - 1.0) < 0.01
        assert artifact.feature_columns

    def test_stated_risk_appetite_leads_both_rankings(self) -> None:
        """The rubric weights appetite highest by a clear margin (0.30 vs 0.20). A
        forest that did not also lean on it hardest would mean the fitted model and
        the documented rule had come apart — which is the whole point of measuring.
        """
        from app.ml.risk import model as risk_model

        _, metrics = risk_model.train(population=1500)
        alignment = metrics["rubric_alignment"]

        assert alignment["ranking_by_share"][0] == "appetite"
        assert alignment["ranking_by_weight"][0] == "appetite"

    def test_the_alignment_result_travels_in_the_registry_row(self, db: Session) -> None:
        """§10.5 — a validation result that has to be recomputed to be read is one
        nobody reads. It belongs on the model record with the rest of the metrics."""
        from app.jobs import ml as ml_jobs

        record = ml_jobs.train_risk(db)
        assert record.metrics is not None
        assert "rubric_alignment" in record.metrics
        assert record.metrics["rubric_alignment"]["method"].startswith("permutation_importance")
