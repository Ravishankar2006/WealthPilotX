"""FR-02 acceptance criteria, plus the §16.2 ownership rule and §11.2 erasure."""

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.financial_profile import FinancialProfile
from app.models.refresh_token import RefreshToken
from app.models.user import User


class TestProfileValidation:
    def test_out_of_range_values_return_422_with_field_errors(
        self, client: TestClient, auth_headers: dict[str, str], valid_profile: dict[str, object]
    ) -> None:
        """Given values out of allowed ranges (negative income, age < 18), the API
        returns 422 with field-level validation errors."""
        response = client.put(
            "/api/v1/user/profile",
            headers=auth_headers,
            json={**valid_profile, "age": 15, "income": "-1000.00"},
        )

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_error"
        assert "age" in error["fields"]
        assert "income" in error["fields"]

    def test_rejects_unknown_enum_value(
        self, client: TestClient, auth_headers: dict[str, str], valid_profile: dict[str, object]
    ) -> None:
        response = client.put(
            "/api/v1/user/profile",
            headers=auth_headers,
            json={**valid_profile, "investment_goal": "SPECULATION"},
        )
        assert response.status_code == 422
        assert "investment_goal" in response.json()["error"]["fields"]

    def test_rejects_zero_investment_horizon(
        self, client: TestClient, auth_headers: dict[str, str], valid_profile: dict[str, object]
    ) -> None:
        response = client.put(
            "/api/v1/user/profile",
            headers=auth_headers,
            json={**valid_profile, "investment_horizon": 0},
        )
        assert response.status_code == 422

    def test_nothing_is_saved_when_validation_fails(
        self,
        client: TestClient,
        db: Session,
        auth_headers: dict[str, str],
        valid_profile: dict[str, object],
    ) -> None:
        client.put("/api/v1/user/profile", headers=auth_headers, json={**valid_profile, "age": 15})
        assert db.scalars(select(FinancialProfile)).all() == []


class TestProfileRoundTrip:
    def test_saved_profile_reads_back_identically(
        self, client: TestClient, auth_headers: dict[str, str], valid_profile: dict[str, object]
    ) -> None:
        created = client.put("/api/v1/user/profile", headers=auth_headers, json=valid_profile)
        assert created.status_code == 200

        fetched = client.get("/api/v1/user/profile", headers=auth_headers)
        assert fetched.status_code == 200

        body = fetched.json()
        assert body["age"] == valid_profile["age"]
        assert Decimal(body["income"]) == Decimal(str(valid_profile["income"]))
        assert Decimal(body["savings"]) == Decimal(str(valid_profile["savings"]))
        assert body["investment_goal"] == valid_profile["investment_goal"]

    def test_put_replaces_rather_than_duplicating(
        self,
        client: TestClient,
        db: Session,
        auth_headers: dict[str, str],
        valid_profile: dict[str, object],
    ) -> None:
        client.put("/api/v1/user/profile", headers=auth_headers, json=valid_profile)
        client.put("/api/v1/user/profile", headers=auth_headers, json={**valid_profile, "age": 41})

        profiles = db.scalars(select(FinancialProfile)).all()
        assert len(profiles) == 1
        assert profiles[0].age == 41

    def test_missing_profile_returns_404(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get("/api/v1/user/profile", headers=auth_headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "profile_not_found"


class TestCompleteness:
    def test_reports_every_field_missing_before_a_profile_exists(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """What FR-03 will call to block risk assessment in Milestone 3."""
        response = client.get("/api/v1/user/profile/completeness", headers=auth_headers)
        assert response.status_code == 200

        body = response.json()
        assert body["complete"] is False
        assert "income" in body["missing_fields"]
        assert len(body["missing_fields"]) == 8

    def test_reports_complete_once_the_profile_is_saved(
        self, client: TestClient, auth_headers: dict[str, str], valid_profile: dict[str, object]
    ) -> None:
        client.put("/api/v1/user/profile", headers=auth_headers, json=valid_profile)
        body = client.get("/api/v1/user/profile/completeness", headers=auth_headers).json()
        assert body["complete"] is True
        assert body["missing_fields"] == []


class TestEncryptionAtRest:
    def test_income_and_savings_are_ciphertext_in_the_database(
        self,
        client: TestClient,
        db: Session,
        auth_headers: dict[str, str],
        valid_profile: dict[str, object],
    ) -> None:
        """Decision 01 — the plaintext figure must not be readable from the column."""
        client.put("/api/v1/user/profile", headers=auth_headers, json=valid_profile)

        raw = db.execute(text("SELECT income, savings FROM financial_profiles")).one()
        assert "82000" not in raw[0]
        assert "25000" not in raw[1]
        assert raw[0].startswith("gAAAAA")  # Fernet token prefix


class TestOwnership:
    def test_one_user_cannot_read_or_write_another_users_profile(
        self, client: TestClient, auth_headers: dict[str, str], valid_profile: dict[str, object]
    ) -> None:
        """§16.2 — users may only read and modify their own resources.

        The route is scoped to the token's subject and never names a user, so a
        second account sees its own (absent) profile, not the first account's."""
        client.put("/api/v1/user/profile", headers=auth_headers, json=valid_profile)

        second = client.post(
            "/api/v1/auth/register",
            json={
                "email": "someone-else@example.com",
                "password": "another-valid-password",
                "tos_accepted": True,
            },
        ).json()
        other_headers = {"Authorization": f"Bearer {second['tokens']['access_token']}"}

        assert client.get("/api/v1/user/profile", headers=other_headers).status_code == 404

        client.put("/api/v1/user/profile", headers=other_headers, json={**valid_profile, "age": 62})
        mine = client.get("/api/v1/user/profile", headers=auth_headers).json()
        assert mine["age"] == valid_profile["age"]


class TestErasure:
    def test_delete_removes_the_account_profile_and_sessions(
        self,
        client: TestClient,
        db: Session,
        auth_headers: dict[str, str],
        registered: dict[str, object],
        valid_profile: dict[str, object],
    ) -> None:
        """§11.2 right to erasure — a hard delete, cascading everything."""
        client.put("/api/v1/user/profile", headers=auth_headers, json=valid_profile)

        response = client.delete("/api/v1/user/profile", headers=auth_headers)
        assert response.status_code == 200

        assert db.scalars(select(User)).all() == []
        assert db.scalars(select(FinancialProfile)).all() == []
        assert db.scalars(select(RefreshToken)).all() == []

    def test_credentials_stop_working_after_erasure(
        self, client: TestClient, auth_headers: dict[str, str], registered: dict[str, object]
    ) -> None:
        client.delete("/api/v1/user/profile", headers=auth_headers)

        login = client.post(
            "/api/v1/auth/login",
            json={"email": registered["email"], "password": registered["password"]},
        )
        assert login.status_code == 401

    def test_an_outstanding_access_token_stops_working_after_erasure(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """The access token stays cryptographically valid until it expires, so the
        user lookup is what must reject it."""
        client.delete("/api/v1/user/profile", headers=auth_headers)
        assert client.get("/api/v1/user/profile", headers=auth_headers).status_code == 401
