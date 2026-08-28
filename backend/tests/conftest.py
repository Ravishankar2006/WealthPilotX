"""Test fixtures.

Tests run against a real PostgreSQL database, not SQLite. The schema depends on
Postgres-specific types (native enums, UUID columns), so a SQLite substitute would
test a different schema than the one that ships.
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# Forced, not defaulted: a compose or shell environment that already sets these
# would otherwise leak development configuration into the suite.
os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-value-at-least-32-characters-long")
os.environ.setdefault("PROFILE_ENCRYPTION_KEY", "test-profile-encryption-key-at-least-32-chars")
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://wpx:wpx@localhost:5432/wpx_test"
)

# This suite truncates tables between tests. Pointing it at a development database
# would silently destroy real data, so the database name must say it is a test one.
_DB_NAME = os.environ["DATABASE_URL"].rsplit("/", 1)[-1].split("?")[0]
if "test" not in _DB_NAME:
    raise RuntimeError(
        f"Refusing to run the test suite against database {_DB_NAME!r}: the suite "
        "truncates tables. Point TEST_DATABASE_URL at a database whose name "
        "contains 'test'."
    )

from app.core.ratelimit import limiter  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402  (registers every model on Base.metadata)

TEST_DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
TestSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    """Truncate between tests so ordering never matters, and reset the in-process
    rate limiter so one test's requests cannot exhaust another's allowance."""
    limiter.reset()
    yield
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE users, financial_profiles, refresh_tokens RESTART IDENTITY CASCADE")
        )


@pytest.fixture
def db() -> Iterator[Session]:
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def credentials() -> dict[str, str]:
    return {
        "email": f"investor-{uuid.uuid4().hex[:10]}@example.com",
        "password": "correct-horse-battery-staple",
    }


@pytest.fixture
def registered(client: TestClient, credentials: dict[str, str]) -> dict[str, object]:
    """A registered account plus its live token pair."""
    response = client.post(
        "/api/v1/auth/register",
        json={**credentials, "tos_accepted": True},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {
        "email": credentials["email"],
        "password": credentials["password"],
        "user": body["user"],
        "tokens": body["tokens"],
    }


@pytest.fixture
def auth_headers(registered: dict[str, object]) -> dict[str, str]:
    tokens = registered["tokens"]
    assert isinstance(tokens, dict)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def valid_profile() -> dict[str, object]:
    return {
        "age": 34,
        "income": "82000.00",
        "savings": "25000.00",
        "risk_appetite": "MODERATE",
        "investment_goal": "GROWTH",
        "investment_horizon": 15,
        "experience": "BEGINNER",
        "financial_literacy": "MEDIUM",
    }


__all__ = ["User"]
