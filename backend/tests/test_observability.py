"""§16.4 metrics and §16.2 response headers.

The privacy test in here is the one that would be embarrassing to omit: a metrics
endpoint is designed to be scraped and retained, so anything that reaches it reaches
a long-lived store. A raw path would put a user's chosen symbol into that store, and
on other routes an id.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import security_headers
from app.services.metrics_service import _percentile, metrics


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    """Counters that carry between tests make every assertion order-dependent."""
    metrics.reset()


class TestPercentile:
    def test_it_is_nearest_rank_not_interpolated(self) -> None:
        values = [float(n) for n in range(1, 101)]
        assert _percentile(values, 0.50) == 50.0
        assert _percentile(values, 0.95) == 95.0
        assert _percentile(values, 0.99) == 99.0

    def test_an_empty_sample_is_zero_not_an_error(self) -> None:
        assert _percentile([], 0.95) == 0.0

    def test_a_single_sample_is_that_sample(self) -> None:
        assert _percentile([7.5], 0.99) == 7.5


class TestRequestCounters:
    def test_traffic_moves_the_counters(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        client.get("/api/v1/market/assets", headers=auth_headers)
        client.get("/api/v1/market/assets", headers=auth_headers)

        body = client.get("/api/v1/metrics", headers=auth_headers).json()

        assert body["requests"]["count"] >= 3
        assert body["routes"]["/api/v1/market/assets"]["count"] == 2
        assert body["routes"]["/api/v1/market/assets"]["latency_ms"]["sampled"] == 2

    def test_a_server_error_is_separated_from_a_client_error(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """A 404 is the API working. Folding it into the error rate makes a healthy
        service look broken and hides the 500s among the noise."""
        client.get("/api/v1/market/NOSUCHSYMBOL", headers=auth_headers)

        body = client.get("/api/v1/metrics", headers=auth_headers).json()

        assert body["requests"]["client_errors"] >= 1
        assert body["requests"]["errors"] == 0
        assert body["requests"]["error_rate"] == 0.0

    def test_it_records_route_templates_not_paths(
        self, client: TestClient, auth_headers: dict[str, str], db: Session, seeded_assets: list
    ) -> None:
        """§11.2 in a place it is easy to forget it applies."""
        client.get("/api/v1/market/SPY", headers=auth_headers)

        body = client.get("/api/v1/metrics", headers=auth_headers).json()
        routes = body["routes"]

        assert "/api/v1/market/{symbol}" in routes
        assert not any("SPY" in route for route in routes)

    def test_an_unrouted_path_does_not_create_a_series_per_url(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Otherwise a scanner walking the URL space is an unbounded memory leak."""
        for suffix in ("alpha", "beta", "gamma"):
            client.get(f"/api/v1/does-not-exist/{suffix}", headers=auth_headers)

        routes = client.get("/api/v1/metrics", headers=auth_headers).json()["routes"]

        assert routes["<unrouted>"]["count"] == 3
        assert not any("alpha" in route for route in routes)

    def test_it_requires_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/metrics").status_code == 401


class TestModelLatency:
    def test_risk_classification_is_timed(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        valid_profile: dict[str, object],
        db: Session,
    ) -> None:
        """§16.4 names model prediction latency specifically, and §16.1 puts a
        5-second budget on it — a budget nobody can check without a number.

        The model is trained and promoted here rather than skipping when none is
        present. A test that skips on an empty database is a test that never runs.
        """
        from app.jobs import ml
        from app.ml import registry
        from app.models.model_record import RISK_MODEL

        record = ml.train_risk(db)
        registry.promote(db, RISK_MODEL, record.version)

        client.put("/api/v1/user/profile", json=valid_profile, headers=auth_headers)
        assert client.post("/api/v1/risk/analyze", headers=auth_headers).status_code == 201

        timers = client.get("/api/v1/metrics", headers=auth_headers).json()["timers"]
        assert timers["risk_classification"]["count"] == 1
        assert timers["risk_classification"]["mean_ms"] > 0
        # §16.1 allows 5 seconds for an ML prediction. Asserted, not assumed.
        assert timers["risk_classification"]["p95_ms"] < 5000


class TestIngestionSuccessRate:
    def test_partial_runs_count_as_failures(self, db: Session) -> None:
        """FR-04 forbids a silent skip. A success rate that scores a run where half
        the symbols failed as a success is that silence with a number on it."""
        from datetime import UTC, datetime

        from app.models.enums import IngestionStatus
        from app.models.ingestion_run import IngestionRun
        from app.services import metrics_service

        now = datetime.now(UTC)
        db.add_all(
            [
                IngestionRun(job="ingest_market", status=IngestionStatus.SUCCESS, started_at=now),
                IngestionRun(job="ingest_market", status=IngestionStatus.PARTIAL, started_at=now),
            ]
        )
        db.commit()

        rate = metrics_service.ingestion_success_rate(db)["jobs"]["ingest_market"]

        assert rate["total"] == 2
        assert rate["succeeded"] == 1
        assert rate["success_rate"] == 0.5

    def test_no_runs_reports_null_rather_than_zero(self, db: Session) -> None:
        from app.services import metrics_service

        assert metrics_service.ingestion_success_rate(db)["jobs"] == {}


class TestSecurityHeaders:
    def test_they_are_present_on_a_success(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        headers = client.get("/api/v1/health", headers=auth_headers).headers

        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert headers["Content-Security-Policy"] == security_headers.API_CSP

    def test_they_survive_an_error_response(self, client: TestClient) -> None:
        """The headers must be applied outside the error handling, or the responses
        that matter most — the ones a client did not expect — are the bare ones."""
        headers = client.get("/api/v1/market/assets").headers

        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"

    def test_hsts_is_not_sent_from_a_local_or_test_stack(self, client: TestClient) -> None:
        """Sent from a local server it pins `localhost` to HTTPS for a year, for
        every project on the machine, with no obvious way to undo it.

        The first version of this guard excluded `{"development", "test"}` and this
        repo's environments are `local | test | staging | production`, so the
        development stack shipped HSTS anyway. Caught by curling the running API,
        not by this test — which is why the parametrised case below now covers every
        name the settings Literal actually permits.
        """
        assert "Strict-Transport-Security" not in client.get("/api/v1/health").headers

    @pytest.mark.parametrize(
        ("environment", "expected"),
        [("local", False), ("test", False), ("staging", True), ("production", True)],
    )
    def test_hsts_covers_every_declared_environment(self, environment: str, expected: bool) -> None:
        middleware = security_headers.SecurityHeadersMiddleware(object(), environment=environment)
        assert middleware.send_hsts is expected

    def test_the_declared_environments_are_the_ones_the_settings_allow(self) -> None:
        """Pins the two lists together. If a new environment is added to the
        settings Literal, this fails rather than silently defaulting it to no HSTS."""
        from typing import get_args

        from app.core.config import Settings

        declared = set(get_args(Settings.model_fields["environment"].annotation))
        assert security_headers.HSTS_ENVIRONMENTS <= declared
        assert declared == {"local", "test", "staging", "production"}

    def test_the_correlation_id_still_comes_back(self, client: TestClient) -> None:
        """Two middlewares now wrap every response; the older contract still holds."""
        assert client.get("/api/v1/health").headers.get("X-Correlation-ID")
