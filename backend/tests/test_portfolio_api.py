"""FR-10 to FR-13 end to end, plus the §16.2 ownership rules."""

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs import ml
from app.ml import registry
from app.models.asset import Asset
from app.models.enums import TrendDirection
from app.models.market_data import MarketData
from app.models.model_record import RISK_MODEL
from app.models.portfolio import PortfolioAsset
from app.models.prediction import Prediction
from app.schemas.risk import MODEL_OUTPUT_DISCLAIMER

BASE = "/api/v1/portfolio"


@pytest.fixture
def market(db: Session, seeded_assets: list[Asset]) -> None:
    """Two years of deterministic prices for every seeded asset, plus predictions.

    Per-asset drift and volatility are keyed off the symbol so the universe has
    genuine cross-sectional variation — a universe where every asset behaves
    identically would let a broken optimizer look correct.
    """
    start = date(2024, 1, 1)
    rows: list[MarketData] = []
    predictions: list[Prediction] = []

    for index, asset in enumerate(seeded_assets):
        rng = np.random.default_rng(1000 + index)
        drift = 0.0001 + (index % 5) * 0.00012
        vol = 0.004 + (index % 7) * 0.0018
        price = 50.0 + index * 3

        for offset in range(560):
            day = start + timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            price *= float(np.exp(rng.normal(drift, vol)))
            rows.append(
                MarketData(
                    asset_id=asset.id,
                    date=day,
                    open=Decimal(str(round(price, 2))),
                    high=Decimal(str(round(price * 1.004, 2))),
                    low=Decimal(str(round(price * 0.996, 2))),
                    close=Decimal(str(round(price, 2))),
                    adj_close=Decimal(str(round(price, 2))),
                    volume=1_000_000,
                    source="test",
                )
            )

        predictions.append(
            Prediction(
                asset_id=asset.id,
                model_version="v1",
                prediction_date=date(2025, 7, 1),
                predicted_return=Decimal(str(round(0.004 + (index % 5) * 0.002, 8))),
                trend=TrendDirection.UP,
                confidence=Decimal("0.55"),
                horizon_days=20,
            )
        )

    db.add_all(rows)
    db.add_all(predictions)
    db.commit()


@pytest.fixture
def ready(
    client: TestClient, auth_headers: dict[str, str], valid_profile: dict, db: Session, market: None
) -> dict[str, str]:
    """A user with a profile, a promoted risk model and a risk assessment."""
    record = ml.train_risk(db)
    registry.promote(db, RISK_MODEL, record.version)
    assert (
        client.put("/api/v1/user/profile", json=valid_profile, headers=auth_headers).status_code
        == 200
    )
    assert client.post("/api/v1/risk/analyze", headers=auth_headers).status_code == 201
    return auth_headers


class TestAuthentication:
    @pytest.mark.parametrize(
        ("method", "path"), [("post", "/generate"), ("get", "/current"), ("get", "/history")]
    )
    def test_unauthenticated_requests_are_rejected(
        self, client: TestClient, method: str, path: str
    ) -> None:
        assert getattr(client, method)(f"{BASE}{path}").status_code == 401


class TestGenerate:
    def test_it_blocks_without_a_risk_assessment(
        self, client: TestClient, auth_headers: dict[str, str], valid_profile: dict, market: None
    ) -> None:
        client.put("/api/v1/user/profile", json=valid_profile, headers=auth_headers)
        response = client.post(f"{BASE}/generate", headers=auth_headers)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "risk_assessment_required"

    def test_it_blocks_on_an_incomplete_profile(
        self, client: TestClient, auth_headers: dict[str, str], market: None
    ) -> None:
        response = client.post(f"{BASE}/generate", headers=auth_headers)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "incomplete_profile"

    def test_weights_sum_to_one(self, client: TestClient, ready: dict[str, str]) -> None:
        """FR-11 acceptance criterion 1, at the API boundary."""
        body = client.post(f"{BASE}/generate", headers=ready).json()
        total = sum(float(h["weight"]) for h in body["holdings"])
        assert abs(total - 1.0) <= 0.001

    def test_every_holding_carries_a_reason(
        self, client: TestClient, ready: dict[str, str]
    ) -> None:
        """FR-13: a reason is attached *before* the recommendation is shown, so the
        response shape must not make it optional in practice."""
        body = client.post(f"{BASE}/generate", headers=ready).json()
        assert body["holdings"]
        for holding in body["holdings"]:
            assert holding["reason"]
            assert holding["symbol"] in holding["reason"]
            assert holding["recommendation_id"]

    def test_it_reports_expected_return_and_risk(
        self, client: TestClient, ready: dict[str, str]
    ) -> None:
        body = client.post(f"{BASE}/generate", headers=ready).json()
        assert float(body["expected_risk"]) > 0
        assert body["model_version"]

    def test_it_records_the_constraints_that_shaped_it(
        self, client: TestClient, ready: dict[str, str]
    ) -> None:
        """ "Why 12% and not 20%?" must be answerable from the response."""
        body = client.post(f"{BASE}/generate", headers=ready).json()
        objective = body["objective"]
        assert "risk_aversion" in objective
        assert "class_bands" in objective
        assert objective["notes"]
        assert "mean-variance optimiser" in body["explanation"]

    def test_it_carries_the_required_disclaimer(
        self, client: TestClient, ready: dict[str, str]
    ) -> None:
        body = client.post(f"{BASE}/generate", headers=ready).json()
        assert body["disclaimer"] == MODEL_OUTPUT_DISCLAIMER

    def test_each_call_creates_a_new_portfolio(
        self, client: TestClient, ready: dict[str, str]
    ) -> None:
        """Immutable once generated: an explanation must still be true later."""
        first = client.post(f"{BASE}/generate", headers=ready).json()
        second = client.post(f"{BASE}/generate", headers=ready).json()
        assert first["id"] != second["id"]

    def test_no_position_exceeds_its_cap(self, client: TestClient, ready: dict[str, str]) -> None:
        body = client.post(f"{BASE}/generate", headers=ready).json()
        cap = body["objective"]["max_weight_per_asset"]
        assert max(float(h["weight"]) for h in body["holdings"]) <= cap + 0.001

    def test_profile_values_never_appear_in_the_response(
        self, client: TestClient, auth_headers: dict[str, str], db: Session, market: None
    ) -> None:
        """§11.2 — a portfolio is derived from income and savings; it must not carry
        them back out.

        Distinctive digit strings, deliberately: the first version of this test used
        the standard fixture's 25000 savings and failed against the substring inside
        a weight of "0.25000000". A PII check that fires on a coincidence is a check
        nobody will trust the next time it fires.
        """
        record = ml.train_risk(db)
        registry.promote(db, RISK_MODEL, record.version)
        client.put(
            "/api/v1/user/profile",
            json={
                "age": 34,
                "income": "87431.00",
                "savings": "61978.00",
                "risk_appetite": "MODERATE",
                "investment_goal": "GROWTH",
                "investment_horizon": 15,
                "experience": "BEGINNER",
                "financial_literacy": "MEDIUM",
            },
            headers=auth_headers,
        )
        client.post("/api/v1/risk/analyze", headers=auth_headers)

        raw = client.post(f"{BASE}/generate", headers=auth_headers).text
        assert "87431" not in raw
        assert "61978" not in raw


class TestCurrentAndHistory:
    def test_no_portfolio_yet_is_a_404(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(f"{BASE}/current", headers=auth_headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "no_portfolio"

    def test_current_returns_the_newest(self, client: TestClient, ready: dict[str, str]) -> None:
        client.post(f"{BASE}/generate", headers=ready)
        newest = client.post(f"{BASE}/generate", headers=ready).json()
        assert client.get(f"{BASE}/current", headers=ready).json()["id"] == newest["id"]

    def test_history_is_newest_first_and_paginated(
        self, client: TestClient, ready: dict[str, str]
    ) -> None:
        for _ in range(3):
            client.post(f"{BASE}/generate", headers=ready)

        body = client.get(f"{BASE}/history?limit=2", headers=ready).json()
        assert set(body) == {"data", "next_cursor"}
        assert len(body["data"]) == 2
        assert body["next_cursor"]

        timestamps = [row["created_at"] for row in body["data"]]
        assert timestamps == sorted(timestamps, reverse=True)

        page_two = client.get(
            f"{BASE}/history?limit=2&cursor={body['next_cursor']}", headers=ready
        ).json()
        assert page_two["data"]
        assert {r["id"] for r in page_two["data"]}.isdisjoint({r["id"] for r in body["data"]})

    def test_a_malformed_cursor_is_a_400(self, client: TestClient, ready: dict[str, str]) -> None:
        response = client.get(f"{BASE}/history?cursor=%FF%FF", headers=ready)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_cursor"


class TestOwnership:
    def test_a_user_never_sees_another_users_portfolio(
        self, client: TestClient, ready: dict[str, str]
    ) -> None:
        """§16.2 — an access-control test, not an afterthought."""
        mine = client.post(f"{BASE}/generate", headers=ready).json()

        other = client.post(
            "/api/v1/auth/register",
            json={
                "email": "portfolio-other@example.com",
                "password": "a-different-password",
                "tos_accepted": True,
            },
        ).json()
        other_headers = {"Authorization": f"Bearer {other['tokens']['access_token']}"}

        assert client.get(f"{BASE}/current", headers=other_headers).status_code == 404
        assert client.get(f"{BASE}/history", headers=other_headers).json()["data"] == []

        # And their explanation endpoint cannot reach my recommendation.
        recommendation_id = mine["holdings"][0]["recommendation_id"]
        response = client.get(
            f"/api/v1/recommendation/{recommendation_id}/explanation", headers=other_headers
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "recommendation_not_found"


class TestExplanation:
    def test_it_returns_the_reason_and_its_context(
        self, client: TestClient, ready: dict[str, str]
    ) -> None:
        """FR-13's endpoint."""
        portfolio = client.post(f"{BASE}/generate", headers=ready).json()
        holding = portfolio["holdings"][0]

        body = client.get(
            f"/api/v1/recommendation/{holding['recommendation_id']}/explanation", headers=ready
        ).json()

        assert body["symbol"] == holding["symbol"]
        assert body["reason"] == holding["reason"]
        assert body["portfolio_id"] == portfolio["id"]
        assert float(body["weight"]) == pytest.approx(float(holding["weight"]))
        assert body["portfolio_explanation"]
        assert body["disclaimer"] == MODEL_OUTPUT_DISCLAIMER

    def test_an_unknown_recommendation_is_a_404(
        self, client: TestClient, ready: dict[str, str]
    ) -> None:
        import uuid

        response = client.get(f"/api/v1/recommendation/{uuid.uuid4()}/explanation", headers=ready)
        assert response.status_code == 404


class TestDatabaseGuarantee:
    def test_the_database_refuses_a_portfolio_whose_weights_do_not_sum_to_one(
        self, client: TestClient, ready: dict[str, str], db: Session
    ) -> None:
        """Judgment call 2: §12 puts the sum-to-1 rule at the application layer, but
        a deferred constraint trigger holds it in the database too — so a future code
        path that forgets to check still cannot store a malformed portfolio."""
        from sqlalchemy.exc import InternalError, ProgrammingError

        portfolio_id = client.post(f"{BASE}/generate", headers=ready).json()["id"]

        holding = db.scalar(
            select(PortfolioAsset).where(PortfolioAsset.portfolio_id == portfolio_id)
        )
        assert holding is not None

        with pytest.raises((InternalError, ProgrammingError), match="outside 1.0"):
            holding.weight = Decimal("0.999")
            db.commit()
        db.rollback()
