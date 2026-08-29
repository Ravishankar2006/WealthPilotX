"""§16.2 — the access-control matrix, asserted rather than assumed.

The individual API test files already check ownership on the surfaces they cover.
This file is the *systematic* version, and it exists because the per-file checks
share a blind spot: each one only covers the endpoint its author was thinking about.
A route added later gets tests for what it returns, and quietly none for who may
read it.

So this enumerates every non-public route from the application itself, and asserts
that each one rejects an anonymous caller. §13.1 states the rule without exception —
Bearer JWT on everything except `/auth/register` and `/auth/login` — and a test that
reads the route table cannot fall behind it.

The M6 additions are the reason it is worth writing now: `/fairness/report` reads
aggregate data derived from every user's financial profile, and `/metrics` describes
the whole instance's traffic. Both are exactly the kind of endpoint that gets added
without anyone asking who should see it.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app

# The only routes §13.1 exempts, plus the unauthenticated system surfaces the PRD
# places outside the rule: `/health` is a liveness probe an orchestrator calls
# without credentials (§16.4), and `/` is the service banner.
PUBLIC_PATHS = {
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/api/v1/health",
    "/",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}


def _routes() -> list[tuple[str, str]]:
    """Every (method, path) the application serves, read from its OpenAPI schema.

    The schema rather than `app.routes`: this FastAPI version keeps included routers
    nested behind an internal `_IncludedRouter` whose children carry their prefix in
    a private context object, so walking the route tree means reproducing FastAPI's
    own prefix assembly — against internals that can change. The schema is the
    documented view of the same table and already has the full paths.
    """
    schema = app.openapi()
    return sorted(
        (method.upper(), path)
        for path, operations in schema.get("paths", {}).items()
        for method in operations
        if method.upper() not in {"HEAD", "OPTIONS"}
    )


def _fill(path: str) -> str:
    """Substitute a syntactically valid value for each path parameter.

    A malformed id would be rejected at validation *before* authentication, and the
    test would pass on a 422 while proving nothing about who may read the route.
    """
    return (
        path.replace("{symbol}", "SPY")
        .replace("{recommendation_id}", str(uuid.uuid4()))
        .replace("{id}", str(uuid.uuid4()))
    )


class TestAnonymousAccess:
    def test_the_route_table_is_not_empty(self) -> None:
        """Guards the rest of the file: a walk that silently found nothing would
        make every parametrised case below vacuous."""
        assert len(_routes()) > 15

    @pytest.mark.parametrize(("method", "path"), _routes())
    def test_every_non_public_route_rejects_an_anonymous_caller(
        self, client: TestClient, method: str, path: str
    ) -> None:
        if path in PUBLIC_PATHS:
            pytest.skip("public by §13.1")

        response = client.request(method, _fill(path))

        assert response.status_code == 401, (
            f"{method} {path} answered {response.status_code} without a token"
        )
        assert response.json()["error"]["code"] == "unauthorized"


class TestOwnership:
    """§16.2: "users can only read/modify their own resources"."""

    @pytest.fixture
    def two_users(self, client: TestClient) -> tuple[dict[str, str], dict[str, str]]:
        headers = []
        for index in range(2):
            response = client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"owner-{index}-{uuid.uuid4().hex[:8]}@example.com",
                    "password": "correct-horse-battery-staple",
                    "tos_accepted": True,
                },
            )
            assert response.status_code == 201, response.text
            token = response.json()["tokens"]["access_token"]
            headers.append({"Authorization": f"Bearer {token}"})
        return headers[0], headers[1]

    def test_a_profile_is_scoped_to_its_owner(
        self,
        client: TestClient,
        two_users: tuple[dict[str, str], dict[str, str]],
        valid_profile: dict[str, object],
    ) -> None:
        """There is no route that names another user's profile, which is the
        cheapest way to satisfy the rule — this pins that property."""
        first, second = two_users
        client.put("/api/v1/user/profile", json=valid_profile, headers=first)

        assert client.get("/api/v1/user/profile", headers=second).status_code == 404

    def test_another_users_recommendation_is_a_404_not_a_403(
        self, client: TestClient, two_users: tuple[dict[str, str], dict[str, str]]
    ) -> None:
        """A 403 confirms the resource exists. Whether someone else holds a
        recommendation is not ours to disclose (§16.2)."""
        _, second = two_users
        response = client.get(f"/api/v1/recommendation/{uuid.uuid4()}/explanation", headers=second)
        assert response.status_code == 404

    def test_an_erased_users_token_stops_working(
        self, client: TestClient, two_users: tuple[dict[str, str], dict[str, str]]
    ) -> None:
        """§11.2's erasure is not a soft delete, so a still-valid access token
        minted before it must not keep working."""
        first, _ = two_users
        assert client.delete("/api/v1/user/profile", headers=first).status_code == 200

        assert client.get("/api/v1/user/profile", headers=first).status_code == 401


class TestAggregateSurfaces:
    """The M6 endpoints that read across users, rather than from one."""

    def test_the_fairness_report_exposes_no_individual_row(
        self, client: TestClient, auth_headers: dict[str, str], db: Session
    ) -> None:
        """One registered user with a profile is the smallest possible population,
        and the one where a leak would be a complete disclosure."""
        from datetime import UTC, datetime
        from decimal import Decimal

        from app.models.enums import (
            FinancialLiteracy,
            InvestmentExperience,
            InvestmentGoal,
            RiskAppetite,
            RiskCategory,
        )
        from app.models.financial_profile import FinancialProfile
        from app.models.risk_assessment import RiskAssessment
        from app.models.user import User

        user = User(
            email="solo@example.com",
            password_hash="x",
            tos_accepted_at=datetime.now(UTC),
        )
        db.add(user)
        db.flush()
        db.add(
            FinancialProfile(
                user_id=user.id,
                age=37,
                income=Decimal("93217.41"),
                savings=Decimal("61843.77"),
                risk_appetite=RiskAppetite.AGGRESSIVE,
                investment_goal=InvestmentGoal.GROWTH,
                investment_horizon=22,
                experience=InvestmentExperience.ADVANCED,
                financial_literacy=FinancialLiteracy.HIGH,
            )
        )
        db.add(
            RiskAssessment(
                user_id=user.id,
                model_version="v1",
                risk_score=Decimal("0.83149"),
                risk_category=RiskCategory.HIGH,
                top_factors=[],
            )
        )
        db.commit()

        raw = client.get("/api/v1/fairness/report", headers=auth_headers).text

        for secret in ("93217", "61843", "0.83149", str(user.id), "solo@example.com"):
            assert secret not in raw, f"{secret!r} reached the fairness report"

    def test_metrics_carry_no_user_identifier(
        self, client: TestClient, auth_headers: dict[str, str], registered: dict[str, object]
    ) -> None:
        """A metrics endpoint is designed to be scraped and retained, so anything
        that reaches it reaches a long-lived store."""
        user = registered["user"]
        assert isinstance(user, dict)

        client.get("/api/v1/market/assets", headers=auth_headers)
        raw = client.get("/api/v1/metrics", headers=auth_headers).text

        assert str(user["id"]) not in raw
        assert str(user["email"]) not in raw
