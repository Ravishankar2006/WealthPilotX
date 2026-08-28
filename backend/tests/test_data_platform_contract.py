"""Constraints that hold across the data platform rather than inside one module.

The §7.3 abstraction is only worth having if it is actually respected. A rule that
lives solely in CLAUDE.md gets broken by the first person in a hurry, so it is
tested here.
"""

import ast
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.logging import JsonFormatter, RedactionFilter
from app.models.asset import Asset
from app.models.enums import AssetClass
from app.providers.base import EconomicDataProvider, MarketDataProvider
from app.providers.fred import FredEconomicDataProvider
from app.providers.synthetic import SyntheticEconomicDataProvider, SyntheticMarketDataProvider
from app.services.ingestion.market import ingest_market_data

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# Data-source SDKs no application module may import. The rule exists because
# §7.3's providers are *sources* — swappable, unofficial, liable to change shape —
# and a direct import re-couples the application to one of them.
#
# pandas and numpy were on this list until M3. They came off deliberately: they are
# general numeric libraries, not data sources, and the ML layer legitimately depends
# on them. Keeping them here would have meant either a growing allow-list or
# pretending the feature pipeline does not use a DataFrame.
VENDOR_MODULES = {"yfinance"}

# Where each import is permitted. Anywhere else is a violation.
ALLOWED = {
    "yfinance": {"providers/yahoo.py"},
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


class TestProviderIsolation:
    def test_no_vendor_sdk_is_imported_outside_its_provider(self) -> None:
        """The whole point of §7.3: when Yahoo changes shape, one file changes.

        A direct `import yfinance` in a service or an endpoint silently re-couples
        the application to an unofficial API, and nothing else would catch it.
        """
        violations: list[str] = []
        for path in sorted(APP_ROOT.rglob("*.py")):
            relative = path.relative_to(APP_ROOT).as_posix()
            for module in _imported_modules(path) & VENDOR_MODULES:
                if relative not in ALLOWED.get(module, set()):
                    violations.append(f"{relative} imports {module}")

        assert not violations, "Vendor SDK imported outside the provider layer: " + "; ".join(
            violations
        )

    @pytest.mark.parametrize(
        "provider",
        [SyntheticMarketDataProvider()],
    )
    def test_market_implementations_satisfy_the_protocol(
        self, provider: MarketDataProvider
    ) -> None:
        assert isinstance(provider, MarketDataProvider)

    @pytest.mark.parametrize(
        "provider",
        [SyntheticEconomicDataProvider(), FredEconomicDataProvider(api_key="x")],
    )
    def test_economic_implementations_satisfy_the_protocol(
        self, provider: EconomicDataProvider
    ) -> None:
        assert isinstance(provider, EconomicDataProvider)

    def test_the_yahoo_provider_satisfies_the_protocol(self) -> None:
        """Imported inside the test so the rest of the suite never loads pandas."""
        from app.providers.yahoo import YahooMarketDataProvider

        assert isinstance(YahooMarketDataProvider(), MarketDataProvider)


class TestIngestionObservability:
    def test_job_logs_carry_no_financial_pii(
        self, db: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        """§11.2 applies to background jobs too. Ingestion has no reason to touch a
        profile, but the redaction filter must still be in force on this path — the
        assertion is cheap and the failure mode is a permanent disclosure."""
        db.add(
            Asset(symbol="TEST", asset_type="ETF", asset_class=AssetClass.EQUITY)  # type: ignore[arg-type]
        )
        db.commit()

        class Provider:
            name = "stub"

            def fetch_daily_bars(self, symbol: str, start: object, end: object) -> list:
                return []

        with caplog.at_level(logging.INFO):
            ingest_market_data(db, Provider())  # type: ignore[arg-type]

        formatter = JsonFormatter()
        redaction = RedactionFilter()
        rendered = []
        for record in caplog.records:
            redaction.filter(record)
            rendered.append(formatter.format(record))

        assert rendered
        assert all("password" not in line or "[redacted]" in line for line in rendered)

    def test_a_run_records_the_correlation_id_it_logged_under(self, db: Session) -> None:
        """§16.4 — a run in the table can be tied back to its log lines."""
        from app.core.logging import correlation_id

        db.add(
            Asset(symbol="TEST", asset_type="ETF", asset_class=AssetClass.EQUITY)  # type: ignore[arg-type]
        )
        db.commit()

        token = correlation_id.set("job-trace-1234")
        try:

            class Provider:
                name = "stub"

                def fetch_daily_bars(self, symbol: str, start: object, end: object) -> list:
                    return []

            run = ingest_market_data(db, Provider())  # type: ignore[arg-type]
        finally:
            correlation_id.reset(token)

        assert run.correlation_id == "job-trace-1234"


class TestHealthIngestionBlock:
    def test_health_reports_ingestion_state(self, client: TestClient) -> None:
        """FR-04's alert surface."""
        body = client.get("/api/v1/health").json()
        assert body["ingestion"]["healthy"] is True
        assert {job["job"] for job in body["ingestion"]["jobs"]} == {
            "ingest_market",
            "ingest_economic",
        }

    def test_a_failed_ingestion_degrades_the_status_but_not_the_http_code(
        self, client: TestClient, db: Session
    ) -> None:
        """503 would take a healthy API out of rotation over a background job. The
        API serves yesterday's stored data perfectly well; the payload is the alert."""
        from app.providers.base import ProviderUnavailableError

        db.add(
            Asset(symbol="TEST", asset_type="ETF", asset_class=AssetClass.EQUITY)  # type: ignore[arg-type]
        )
        db.commit()

        class Broken:
            name = "stub"

            def fetch_daily_bars(self, symbol: str, start: object, end: object) -> list:
                raise ProviderUnavailableError("down")

        import app.providers.retry as retry_module

        original = retry_module.time.sleep
        retry_module.time.sleep = lambda _: None  # type: ignore[assignment]
        try:
            ingest_market_data(db, Broken())  # type: ignore[arg-type]
        finally:
            retry_module.time.sleep = original  # type: ignore[assignment]

        response = client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["database"] == "up"
        assert body["ingestion"]["healthy"] is False
