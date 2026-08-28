"""The §13.1 conventions every endpoint inherits, and the §16.4 observability
guarantees. These are cross-cutting, so they are tested once here rather than
repeated per endpoint."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.logging import JsonFormatter, RedactionFilter, scrub
from app.core.ratelimit import limiter


class TestErrorEnvelope:
    @pytest.mark.parametrize(
        ("method", "path", "expected_status"),
        [
            ("get", "/api/v1/user/profile", 401),
            ("get", "/api/v1/nonexistent", 404),
        ],
    )
    def test_errors_share_one_shape(
        self, client: TestClient, method: str, path: str, expected_status: int
    ) -> None:
        response = getattr(client, method)(path)
        assert response.status_code == expected_status

        body = response.json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message", "fields"}
        assert isinstance(body["error"]["code"], str)
        assert isinstance(body["error"]["message"], str)

    def test_validation_errors_carry_field_detail(self, client: TestClient) -> None:
        response = client.post("/api/v1/auth/register", json={"email": "nope"})
        assert response.status_code == 422

        fields = response.json()["error"]["fields"]
        assert "email" in fields
        assert "password" in fields
        assert isinstance(fields["email"], list)


class TestCorrelationId:
    def test_inbound_correlation_id_is_echoed(self, client: TestClient) -> None:
        response = client.get("/api/v1/health", headers={"X-Correlation-ID": "trace-abc-123"})
        assert response.headers["X-Correlation-ID"] == "trace-abc-123"

    def test_one_is_generated_when_absent(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")
        assert len(response.headers["X-Correlation-ID"]) == 36  # uuid4


class TestRateLimiting:
    def test_exceeding_the_credential_bucket_returns_429(self, client: TestClient) -> None:
        limiter.reset()
        payload = {"email": "rate@example.com", "password": "some-password-here"}

        statuses = [client.post("/api/v1/auth/login", json=payload).status_code for _ in range(12)]

        assert 429 in statuses
        limited = client.post("/api/v1/auth/login", json=payload)
        assert limited.json()["error"]["code"] == "rate_limited"


class TestLogRedaction:
    """§11.2 — financial fields and credentials must never reach a log sink."""

    def test_scrub_redacts_sensitive_dict_keys(self) -> None:
        cleaned = scrub({"income": 82000, "savings": 25000, "age": 34})
        assert cleaned["income"] == "[redacted]"
        assert cleaned["savings"] == "[redacted]"
        assert cleaned["age"] == 34

    def test_scrub_redacts_nested_values(self) -> None:
        cleaned = scrub({"profile": {"income": 1, "goal": "GROWTH"}})
        assert cleaned["profile"]["income"] == "[redacted]"
        assert cleaned["profile"]["goal"] == "GROWTH"

    @pytest.mark.parametrize(
        "message",
        [
            "saving income=82000 for user",
            'payload {"savings": 25000}',
            "auth password='hunter2' rejected",
        ],
    )
    def test_scrub_redacts_inline_values_in_free_text(self, message: str) -> None:
        assert "82000" not in scrub(message)
        assert "25000" not in scrub(message)
        assert "hunter2" not in scrub(message)

    def test_formatter_and_filter_strip_sensitive_extras(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="profile_saved",
            args=None,
            exc_info=None,
        )
        record.income = 82000
        record.user_id = "abc-123"

        assert RedactionFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))

        assert payload["income"] == "[redacted]"
        assert payload["user_id"] == "abc-123"
        assert payload["level"] == "info"

    def test_submitted_income_never_appears_in_captured_logs(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        valid_profile: dict[str, object],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The end-to-end version of QA step 9."""
        formatter = JsonFormatter()
        redaction = RedactionFilter()

        with caplog.at_level(logging.DEBUG):
            caplog.handler.addFilter(redaction)
            client.put("/api/v1/user/profile", headers=auth_headers, json=valid_profile)

        rendered = "\n".join(formatter.format(record) for record in caplog.records)
        assert "82000" not in rendered
        assert "25000" not in rendered


class TestHealth:
    def test_health_reports_database_state(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")
        assert response.status_code == 200

        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "up"
        assert body["environment"] == get_settings().environment

    def test_health_needs_no_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/health").status_code == 200


class TestVersioning:
    def test_endpoints_live_under_the_v1_prefix(self, client: TestClient) -> None:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/health").status_code == 404

    def test_root_carries_the_required_disclaimer(self, client: TestClient) -> None:
        """§17.1 — the disclaimer travels with the API, not only the UI."""
        body = client.get("/").json()
        assert "does not provide licensed financial" in body["disclaimer"]
