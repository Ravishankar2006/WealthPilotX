"""Test fixtures.

Tests run against a real PostgreSQL database, not SQLite. The schema depends on
Postgres-specific types (native enums, UUID columns), so a SQLite substitute would
test a different schema than the one that ships.
"""

import os
import tempfile
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

# Forced, not defaulted: a compose or shell environment that already sets these
# would otherwise leak development configuration into the suite.
os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-value-at-least-32-characters-long")
os.environ.setdefault("PROFILE_ENCRYPTION_KEY", "test-profile-encryption-key-at-least-32-chars")
# §7.3 — the suite never touches a third-party API. A red build caused by Yahoo
# having a bad afternoon teaches nobody anything, and the synthetic providers are
# seeded, so tests can assert on values rather than on "some rows appeared".
os.environ["MARKET_DATA_PROVIDER"] = "synthetic"
os.environ["ECONOMIC_DATA_PROVIDER"] = "synthetic"

# Model artifacts go to a scratch directory, never the mounted volume the running
# stack serves from — a test run must not be able to replace a promoted model.
_ARTIFACT_DIR = tempfile.mkdtemp(prefix="wpx-test-artifacts-")
os.environ["MODEL_ARTIFACT_DIR"] = _ARTIFACT_DIR
# 20k profiles per training run would dominate the suite; 2k reproduces the same
# behaviour in a fraction of the time.
os.environ["RISK_TRAINING_POPULATION"] = "2000"

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
from app.models import Asset, User  # noqa: E402  (registers every model on Base.metadata)
from app.providers.synthetic import (  # noqa: E402
    SyntheticEconomicDataProvider,
    SyntheticMarketDataProvider,
)

TEST_DATABASE_URL = os.environ["DATABASE_URL"]


def _ensure_database_exists(url: str) -> None:
    """Create the test database if it is not there yet.

    Without this, a fresh clone (or anyone who has just run `docker compose down
    -v`) gets a connection error instead of a test run, and has to know to create
    the database by hand. The name guard above already established that this is a
    test database, so creating it is safe.
    """
    from sqlalchemy import URL, make_url

    target: URL = make_url(url)
    admin = create_engine(
        target.set(database="postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True
    )
    try:
        with admin.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            ).scalar()
            if not exists:
                # Identifier, so it cannot be parameterised; the name guard above
                # plus this character check keep it safe to interpolate.
                assert target.database and target.database.replace("_", "").isalnum()
                connection.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin.dispose()


_ensure_database_exists(TEST_DATABASE_URL)

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
            text(
                "TRUNCATE users, financial_profiles, refresh_tokens, "
                "assets, market_data, economic_indicators, ingestion_runs, "
                "models, model_monitoring, risk_assessments, predictions, "
                "portfolios, portfolio_assets, recommendations "
                "RESTART IDENTITY CASCADE"
            )
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
def seeded_assets(db: Session) -> list[Asset]:
    """The tracked universe, seeded once for a test that needs assets to exist."""
    from app.services.ingestion.seed import seed_assets

    seed_assets(db)
    return list(db.scalars(select(Asset).order_by(Asset.symbol)))


@pytest.fixture
def market_provider() -> SyntheticMarketDataProvider:
    return SyntheticMarketDataProvider()


@pytest.fixture
def economic_provider() -> SyntheticEconomicDataProvider:
    return SyntheticEconomicDataProvider()


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


__all__ = ["Asset", "User"]
