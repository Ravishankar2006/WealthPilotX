"""Application settings.

Every secret is environment-supplied (PRD §16.2). The app refuses to boot when a
required secret is missing rather than falling back to a development default —
a default secret that reaches production is worse than a crash on startup.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False

    database_url: PostgresDsn

    # Secrets — no defaults, deliberately. Missing values fail validation at import.
    jwt_secret: str = Field(min_length=32)
    profile_encryption_key: str = Field(min_length=32)

    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7

    rate_limit_per_minute: int = 100
    rate_limit_expensive_per_minute: int = 10

    # --- Data platform (M2) ---
    # Which §7.3 implementation runs. "synthetic" needs no network and no keys, so
    # it is the default: a fresh clone gets a working stack before anyone has
    # signed up for an API key.
    market_data_provider: Literal["yahoo", "synthetic"] = "synthetic"
    economic_data_provider: Literal["fred", "synthetic"] = "synthetic"

    # Optional by design — required only when economic_data_provider is "fred",
    # and the provider raises a configuration error rather than the app refusing
    # to boot, because the API serves stored data perfectly well without it.
    fred_api_key: str | None = None

    # How far back a --backfill run reaches when no explicit window is given.
    ingestion_backfill_days: int = 730

    # FR-04: a tracked symbol should have a row for the latest trading day. Past
    # this many hours without one, /health reports the data as stale. 48 rather
    # than 24 so a normal weekend does not raise an alert every Sunday.
    market_data_stale_after_hours: int = 48

    # --- ML (M3) ---
    # Where model artifacts live (§10.5). A mounted directory, not S3: no deployment
    # target is chosen yet, and the registry row carries the path either way.
    model_artifact_dir: str = "/srv/artifacts"

    # Training population for the risk classifier. Tests override it downward.
    risk_training_population: int = 20_000

    # FR-06: absolute daily log return beyond this is counted as an outlier in the
    # quality report. It is a reporting threshold, not a filter — see decision 4 of
    # the Phase 2 plan; rows are flagged and kept, never dropped.
    outlier_log_return_threshold: float = 0.25

    # Kept as a raw string: pydantic-settings JSON-decodes list-typed fields from
    # the environment before any validator runs, so a comma-separated value would
    # fail to parse. `cors_origins` below is the parsed form callers use.
    cors_origins_raw: str = Field(default="http://localhost:5173", alias="CORS_ORIGINS")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @property
    def sqlalchemy_url(self) -> str:
        return str(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
