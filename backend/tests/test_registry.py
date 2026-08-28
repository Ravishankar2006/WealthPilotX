"""§10.5 — model lifecycle, versioning and the promotion gate.

The gate is the point of this file. A registry that records models but promotes
whatever it is handed provides governance theatre, not governance.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.ml import artifacts, registry
from app.models.enums import ModelStatus
from app.models.model_record import PREDICTION_MODEL, RISK_MODEL

NAME = RISK_MODEL


@dataclass
class DummyArtifact:
    """Stands in for a fitted model — the registry does not care what it holds."""

    marker: str = "fitted"


def _register(db: Session, metrics: dict, name: str = NAME):
    return registry.register(
        db,
        name=name,
        payload=DummyArtifact(),
        metrics=metrics,
        training_start=None,
        training_end=None,
    )


def _good(f1: float = 0.9) -> dict:
    return {"f1_macro": f1, "beats_baseline": True}


class TestRegistration:
    def test_a_new_model_starts_as_an_experiment(self, db: Session) -> None:
        """Training never promotes. §10.5 requires manual review in the MVP."""
        record = _register(db, _good())
        assert record.status is ModelStatus.EXPERIMENT

    def test_versions_increment_per_name(self, db: Session) -> None:
        assert _register(db, _good()).version == "v1"
        assert _register(db, _good()).version == "v2"
        assert _register(db, {"rmse": 0.1}, PREDICTION_MODEL).version == "v1"

    def test_the_artifact_is_written_and_checksummed(self, db: Session) -> None:
        record = _register(db, _good())
        assert record.artifact_checksum
        assert artifacts.load(record.artifact_path, record.artifact_checksum).marker == "fitted"

    def test_metrics_are_persisted_not_merely_printed(self, db: Session) -> None:
        """§18 — a metric that only reached stdout cannot be compared at promotion."""
        record = _register(db, _good(0.87))
        assert record.metrics is not None
        assert record.metrics["f1_macro"] == 0.87


class TestArtifactIntegrity:
    def test_a_tampered_artifact_is_refused(self, db: Session) -> None:
        """A stale file on a shared volume otherwise serves predictions the stored
        metrics never described."""
        record = _register(db, _good())
        with open(record.artifact_path, "ab") as handle:
            handle.write(b"tampered")

        with pytest.raises(ValueError, match="does not match its registry checksum"):
            artifacts.load(record.artifact_path, record.artifact_checksum)

    def test_a_missing_artifact_gives_an_actionable_error(self, db: Session) -> None:
        import os

        record = _register(db, _good())
        os.remove(record.artifact_path)

        with pytest.raises(FileNotFoundError, match="re-train or restore"):
            artifacts.load(record.artifact_path, record.artifact_checksum)


class TestPromotionGate:
    def test_the_first_model_must_beat_the_naive_baseline(self, db: Session) -> None:
        """§10.5's first-release rule."""
        record = _register(db, {"f1_macro": 0.4, "beats_baseline": False})

        with pytest.raises(AppError) as caught:
            registry.promote(db, NAME, record.version)

        assert caught.value.status_code == 409
        assert "did not beat the naive baseline" in caught.value.message
        db.refresh(record)
        assert record.status is ModelStatus.EXPERIMENT

    def test_a_first_model_that_beats_the_baseline_is_promoted(self, db: Session) -> None:
        record = _register(db, _good())
        promoted = registry.promote(db, NAME, record.version)
        assert promoted.status is ModelStatus.PRODUCTION

    def test_a_better_model_replaces_and_retires_the_incumbent(self, db: Session) -> None:
        first = _register(db, _good(0.80))
        registry.promote(db, NAME, first.version)

        second = _register(db, _good(0.91))
        registry.promote(db, NAME, second.version)

        db.refresh(first)
        db.refresh(second)
        assert second.status is ModelStatus.PRODUCTION
        assert first.status is ModelStatus.RETIRED

    def test_a_worse_model_is_refused_and_changes_nothing(self, db: Session) -> None:
        """The gate. Without this the registry is a changelog."""
        first = _register(db, _good(0.91))
        registry.promote(db, NAME, first.version)

        worse = _register(db, _good(0.62))
        with pytest.raises(AppError) as caught:
            registry.promote(db, NAME, worse.version)

        assert caught.value.status_code == 409
        assert "does not beat the incumbent" in caught.value.message

        db.refresh(first)
        db.refresh(worse)
        assert first.status is ModelStatus.PRODUCTION
        assert worse.status is ModelStatus.EXPERIMENT

    def test_lower_is_better_for_the_prediction_model(self, db: Session) -> None:
        """RMSE, not F1 — the direction is per-model and declared up front, so a
        promotion cannot be justified by whichever number happened to improve."""
        first = _register(db, {"rmse": 0.05, "beats_baseline": True}, PREDICTION_MODEL)
        registry.promote(db, PREDICTION_MODEL, first.version)

        worse = _register(db, {"rmse": 0.09, "beats_baseline": True}, PREDICTION_MODEL)
        with pytest.raises(AppError):
            registry.promote(db, PREDICTION_MODEL, worse.version)

        better = _register(db, {"rmse": 0.03, "beats_baseline": True}, PREDICTION_MODEL)
        assert (
            registry.promote(db, PREDICTION_MODEL, better.version).status is ModelStatus.PRODUCTION
        )

    def test_a_model_with_no_comparable_metric_is_refused(self, db: Session) -> None:
        record = _register(db, {"beats_baseline": True})
        with pytest.raises(AppError, match="no 'f1_macro' metric"):
            registry.promote(db, NAME, record.version)

    def test_force_overrides_the_gate(self, db: Session) -> None:
        """The escape hatch for a genuinely incomparable metric — and the path by
        which a worse model reaches production, hence the loud log line."""
        first = _register(db, _good(0.91))
        registry.promote(db, NAME, first.version)

        worse = _register(db, _good(0.40))
        assert (
            registry.promote(db, NAME, worse.version, force=True).status is ModelStatus.PRODUCTION
        )

    def test_promoting_an_already_promoted_model_is_a_no_op(self, db: Session) -> None:
        record = _register(db, _good())
        registry.promote(db, NAME, record.version)
        assert registry.promote(db, NAME, record.version).status is ModelStatus.PRODUCTION

    def test_promoting_an_unknown_version_is_a_404(self, db: Session) -> None:
        with pytest.raises(AppError) as caught:
            registry.promote(db, NAME, "v99")
        assert caught.value.status_code == 404


class TestResolution:
    def test_resolution_returns_the_promoted_artifact(self, db: Session) -> None:
        record = _register(db, _good())
        registry.promote(db, NAME, record.version)

        loaded = registry.resolve_production(db, NAME)
        assert loaded.version == record.version
        assert loaded.payload.marker == "fitted"

    def test_no_production_model_is_a_503_not_a_500(self, db: Session) -> None:
        """The system is fine; it is simply not ready to serve this surface, and the
        operator action is to promote a model."""
        with pytest.raises(registry.ModelNotAvailableError) as caught:
            registry.resolve_production(db, NAME)

        assert caught.value.status_code == 503
        assert "Train and promote one" in caught.value.message

    def test_an_experiment_is_never_served(self, db: Session) -> None:
        _register(db, _good())
        with pytest.raises(registry.ModelNotAvailableError):
            registry.resolve_production(db, NAME)
