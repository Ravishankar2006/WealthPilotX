"""FR-03 endpoints end to end, plus the §16.2 ownership rules."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.jobs import ml
from app.ml import registry
from app.models.model_record import RISK_MODEL
from app.schemas.risk import MODEL_OUTPUT_DISCLAIMER

BASE = "/api/v1/risk"


@pytest.fixture
def production_risk_model(db: Session) -> str:
    """A trained, promoted risk model — what every served assessment needs."""
    record = ml.train_risk(db)
    registry.promote(db, RISK_MODEL, record.version)
    return record.version


@pytest.fixture
def with_profile(client: TestClient, auth_headers: dict[str, str], valid_profile: dict) -> None:
    assert (
        client.put("/api/v1/user/profile", json=valid_profile, headers=auth_headers).status_code
        == 200
    )


class TestAuthentication:
    @pytest.mark.parametrize(("method", "path"), [("post", "/analyze"), ("get", "/latest")])
    def test_unauthenticated_requests_are_rejected(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = getattr(client, method)(f"{BASE}{path}")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


class TestAnalyze:
    def test_an_incomplete_profile_blocks_the_request_and_lists_what_is_missing(
        self, client: TestClient, auth_headers: dict[str, str], production_risk_model: str
    ) -> None:
        """FR-02 acceptance criterion 1, verbatim."""
        response = client.post(f"{BASE}/analyze", headers=auth_headers)

        assert response.status_code == 422
        body = response.json()["error"]
        assert body["code"] == "incomplete_profile"
        assert "age" in body["fields"]["missing_fields"]
        assert "income" in body["fields"]["missing_fields"]

    def test_it_returns_category_score_and_three_factors(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        with_profile: None,
        production_risk_model: str,
    ) -> None:
        """FR-03 acceptance criterion 1."""
        response = client.post(f"{BASE}/analyze", headers=auth_headers)
        assert response.status_code == 201, response.text

        body = response.json()
        assert body["risk_category"] in {"LOW", "MEDIUM", "HIGH"}
        assert 0 <= float(body["risk_score"]) <= 1
        assert len(body["top_factors"]) == 3
        assert body["model_version"] == production_risk_model

    def test_repeated_calls_are_deterministic(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        with_profile: None,
        production_risk_model: str,
    ) -> None:
        """FR-03 acceptance criterion 2, at the API boundary rather than in the
        model — this is the level a user actually experiences."""
        first = client.post(f"{BASE}/analyze", headers=auth_headers).json()
        second = client.post(f"{BASE}/analyze", headers=auth_headers).json()

        assert first["risk_category"] == second["risk_category"]
        assert first["risk_score"] == second["risk_score"]
        assert first["top_factors"] == second["top_factors"]

    def test_every_response_carries_the_required_disclaimer(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        with_profile: None,
        production_risk_model: str,
    ) -> None:
        """§17.1 — on every recommendation and prediction view, in the payload so an
        API consumer cannot present the output without having been given it."""
        body = client.post(f"{BASE}/analyze", headers=auth_headers).json()
        assert body["disclaimer"] == MODEL_OUTPUT_DISCLAIMER
        assert "not financial advice" in body["disclaimer"]

    def test_no_production_model_is_a_503(
        self, client: TestClient, auth_headers: dict[str, str], with_profile: None
    ) -> None:
        response = client.post(f"{BASE}/analyze", headers=auth_headers)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "model_unavailable"

    def test_the_profile_values_never_appear_in_the_response(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        with_profile: None,
        production_risk_model: str,
    ) -> None:
        """§11.2 — the assessment is derived from income and savings; it must not
        carry them back out."""
        raw = client.post(f"{BASE}/analyze", headers=auth_headers).text
        assert "82000" not in raw
        assert "25000" not in raw


class TestLatest:
    def test_it_returns_the_most_recent_assessment(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        with_profile: None,
        production_risk_model: str,
    ) -> None:
        created = client.post(f"{BASE}/analyze", headers=auth_headers).json()
        latest = client.get(f"{BASE}/latest", headers=auth_headers).json()
        assert latest["id"] == created["id"]

    def test_no_assessment_yet_is_a_404(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(f"{BASE}/latest", headers=auth_headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "no_risk_assessment"

    def test_a_user_never_sees_another_users_assessment(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        with_profile: None,
        production_risk_model: str,
        credentials: dict[str, str],
    ) -> None:
        """§16.2 — the access-control test, which the PRD calls part of the suite
        rather than an afterthought."""
        mine = client.post(f"{BASE}/analyze", headers=auth_headers).json()

        other = client.post(
            "/api/v1/auth/register",
            json={
                "email": "other-user@example.com",
                "password": "a-different-password",
                "tos_accepted": True,
            },
        ).json()
        other_headers = {"Authorization": f"Bearer {other['tokens']['access_token']}"}

        # They have no assessment of their own, and cannot reach mine.
        assert client.get(f"{BASE}/latest", headers=other_headers).status_code == 404
        assert client.get(f"{BASE}/latest", headers=auth_headers).json()["id"] == mine["id"]


class TestRateLimiting:
    """§13.1 — 10 requests/minute **per user** on /risk/analyze."""

    def test_the_expensive_bucket_is_keyed_per_user_not_per_address(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        with_profile: None,
        production_risk_model: str,
        valid_profile: dict,
    ) -> None:
        """Regression.

        `RateLimit` keys on `request.state.user_id`, which `get_current_user` sets.
        Declared as a route-level dependency it ran *before* that, so the key fell
        back to the source address and the limit became per-IP: two people behind one
        office NAT shared a single 10/min budget, and either could exhaust the
        other's. `UserRateLimit` takes `CurrentUser` as a parameter so the user is
        always resolved first.
        """
        limit = get_settings().rate_limit_expensive_per_minute

        # Exhaust the first user's allowance.
        codes = [
            client.post(f"{BASE}/analyze", headers=auth_headers).status_code
            for _ in range(limit + 2)
        ]
        assert 429 in codes, "the expensive bucket did not engage at all"

        # A second user, same client address, must have their own budget.
        other = client.post(
            "/api/v1/auth/register",
            json={
                "email": "second-user@example.com",
                "password": "a-second-password-here",
                "tos_accepted": True,
            },
        ).json()
        other_headers = {"Authorization": f"Bearer {other['tokens']['access_token']}"}
        client.put("/api/v1/user/profile", json=valid_profile, headers=other_headers)

        response = client.post(f"{BASE}/analyze", headers=other_headers)
        assert response.status_code != 429, (
            "a second user was rate-limited by the first user's requests — "
            "the bucket is keyed by address, not by user"
        )
