"""FR-01 acceptance criteria, verbatim from PRD §9."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.refresh_token import RefreshToken
from app.models.user import User


class TestRegistration:
    def test_new_email_creates_account_with_hashed_password(
        self, client: TestClient, db: Session, credentials: dict[str, str]
    ) -> None:
        """Given a new email, the account is created and the password is stored
        hashed — never in plaintext."""
        response = client.post("/api/v1/auth/register", json={**credentials, "tos_accepted": True})

        assert response.status_code == 201
        body = response.json()
        assert body["user"]["email"] == credentials["email"]
        assert body["tokens"]["access_token"]
        assert body["tokens"]["refresh_token"]
        # The password must not echo back in any form.
        assert credentials["password"] not in response.text

        user = db.scalar(select(User).where(User.email == credentials["email"]))
        assert user is not None
        assert user.password_hash != credentials["password"]
        assert user.password_hash.startswith("$argon2")
        assert user.tos_accepted_at is not None

    def test_duplicate_email_returns_409(
        self, client: TestClient, credentials: dict[str, str]
    ) -> None:
        """Given a duplicate email, the API returns 409 with a clear message."""
        client.post("/api/v1/auth/register", json={**credentials, "tos_accepted": True})
        response = client.post("/api/v1/auth/register", json={**credentials, "tos_accepted": True})

        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "email_already_registered"
        assert "already exists" in error["message"]

    def test_duplicate_email_is_case_insensitive(
        self, client: TestClient, credentials: dict[str, str]
    ) -> None:
        client.post("/api/v1/auth/register", json={**credentials, "tos_accepted": True})
        response = client.post(
            "/api/v1/auth/register",
            json={**credentials, "email": credentials["email"].upper(), "tos_accepted": True},
        )
        assert response.status_code == 409

    def test_rejects_registration_without_accepting_terms(
        self, client: TestClient, credentials: dict[str, str]
    ) -> None:
        """§17.1 — terms must be accepted at registration."""
        response = client.post("/api/v1/auth/register", json={**credentials, "tos_accepted": False})
        assert response.status_code == 422
        assert "tos_accepted" in response.json()["error"]["fields"]

    def test_rejects_short_password(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "short@example.com", "password": "abc", "tos_accepted": True},
        )
        assert response.status_code == 422
        assert "password" in response.json()["error"]["fields"]

    def test_rejects_malformed_email(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "not-an-email",
                "password": "a-long-enough-password",
                "tos_accepted": True,
            },
        )
        assert response.status_code == 422
        assert "email" in response.json()["error"]["fields"]


class TestLogin:
    def test_valid_credentials_issue_token_pair(
        self, client: TestClient, registered: dict[str, object]
    ) -> None:
        """Given valid credentials, a JWT access token and a refresh token are issued."""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": registered["email"], "password": registered["password"]},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"].count(".") == 2  # header.payload.signature
        assert body["refresh_token"]
        assert body["expires_at"]

    def test_wrong_password_returns_401(
        self, client: TestClient, registered: dict[str, object]
    ) -> None:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": registered["email"], "password": "definitely-not-the-password"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"

    def test_unknown_email_returns_the_same_error_as_a_wrong_password(
        self, client: TestClient
    ) -> None:
        """The response must not distinguish the two, or it becomes an
        account-enumeration oracle."""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "some-password-value"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"

    def test_login_is_case_insensitive_on_email(
        self, client: TestClient, registered: dict[str, object]
    ) -> None:
        email = registered["email"]
        assert isinstance(email, str)
        response = client.post(
            "/api/v1/auth/login",
            json={"email": email.upper(), "password": registered["password"]},
        )
        assert response.status_code == 200


class TestProtectedEndpoints:
    def test_missing_token_returns_401(self, client: TestClient) -> None:
        """Given a missing token, any protected endpoint returns 401."""
        response = client.get("/api/v1/user/profile")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"

    def test_malformed_token_returns_401(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/user/profile", headers={"Authorization": "Bearer not-a-real-jwt"}
        )
        assert response.status_code == 401

    def test_expired_token_returns_401(
        self, client: TestClient, registered: dict[str, object]
    ) -> None:
        """Given an expired token, a protected endpoint returns 401."""
        import uuid
        from datetime import UTC, datetime, timedelta

        import jwt

        from app.core.config import get_settings

        settings = get_settings()
        user = registered["user"]
        assert isinstance(user, dict)
        expired = jwt.encode(
            {
                "sub": user["id"],
                "type": "access",
                "iat": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
                "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
                "jti": str(uuid.uuid4()),
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )

        response = client.get(
            "/api/v1/user/profile", headers={"Authorization": f"Bearer {expired}"}
        )
        assert response.status_code == 401
        assert "expired" in response.json()["error"]["message"].lower()

    def test_refresh_token_rejected_where_an_access_token_is_required(
        self, client: TestClient, registered: dict[str, object]
    ) -> None:
        """Token types must not be interchangeable."""
        tokens = registered["tokens"]
        assert isinstance(tokens, dict)
        response = client.get(
            "/api/v1/user/profile",
            headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
        )
        assert response.status_code == 401


class TestRefreshRotation:
    def test_refresh_issues_a_new_pair_and_retires_the_old_token(
        self, client: TestClient, registered: dict[str, object]
    ) -> None:
        tokens = registered["tokens"]
        assert isinstance(tokens, dict)
        original = tokens["refresh_token"]

        response = client.post("/api/v1/auth/refresh", json={"refresh_token": original})
        assert response.status_code == 200
        rotated = response.json()
        assert rotated["refresh_token"] != original

        # The new token works.
        assert (
            client.get(
                "/api/v1/user/profile/completeness",
                headers={"Authorization": f"Bearer {rotated['access_token']}"},
            ).status_code
            == 200
        )

    def test_reusing_a_rotated_token_revokes_the_whole_family(
        self, client: TestClient, db: Session, registered: dict[str, object]
    ) -> None:
        """Decision 02: presenting an already-rotated token is treated as theft, so
        every token descended from that login is revoked."""
        tokens = registered["tokens"]
        assert isinstance(tokens, dict)
        original = tokens["refresh_token"]

        rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": original}).json()

        # Replay the retired token.
        replay = client.post("/api/v1/auth/refresh", json={"refresh_token": original})
        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "refresh_token_reused"

        # The token issued in between is now dead too.
        followup = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
        )
        assert followup.status_code == 401

        live = db.scalars(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))).all()
        assert live == []

    def test_unknown_refresh_token_returns_401(self, client: TestClient) -> None:
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": "made-up-token"})
        assert response.status_code == 401


class TestLogout:
    def test_logout_revokes_the_refresh_token(
        self, client: TestClient, registered: dict[str, object]
    ) -> None:
        tokens = registered["tokens"]
        assert isinstance(tokens, dict)
        refresh_token = tokens["refresh_token"]

        assert (
            client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token}).status_code
            == 200
        )

        after = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert after.status_code == 401

    def test_logout_with_an_unknown_token_still_succeeds(self, client: TestClient) -> None:
        """Reporting failure here would leak which tokens exist."""
        response = client.post("/api/v1/auth/logout", json={"refresh_token": "never-issued"})
        assert response.status_code == 200


class TestRefreshTokenStorage:
    def test_refresh_tokens_are_stored_only_as_hashes(
        self, client: TestClient, db: Session, registered: dict[str, object]
    ) -> None:
        tokens = registered["tokens"]
        assert isinstance(tokens, dict)
        raw = tokens["refresh_token"]
        assert isinstance(raw, str)

        stored = db.scalars(select(RefreshToken)).all()
        assert len(stored) == 1
        assert stored[0].token_hash != raw
        assert len(stored[0].token_hash) == 64  # sha256 hex
