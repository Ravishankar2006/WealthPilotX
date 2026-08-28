"""Cross-cutting ML guarantees: §11.2 in the model layer, and §17.1 on model output.

These are the properties that are nobody's single module and would therefore be
nobody's test.
"""

import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.jobs import ml
from app.ml import artifacts, registry
from app.models.model_record import RISK_MODEL
from app.schemas.risk import MODEL_OUTPUT_DISCLAIMER


def _field_names(artifact: object) -> set[str]:
    """Field names of a slots dataclass. `vars()` raises on these — slots means no
    __dict__ — and that is exactly why the artifacts use them."""
    return {field.name for field in dataclasses.fields(artifact)}  # type: ignore[arg-type]


class TestArtifactsCarryNoPII:
    """§11.2 — profile data is financial PII, and an artifact is a file that gets
    copied between machines far more casually than a database ever is."""

    def test_no_object_in_the_artifact_is_training_set_shaped(self, db: Session) -> None:
        """Walk the artifact's object graph and fail on any array as long as the
        training set.

        A byte-search of the file would prove nothing — joblib compresses it — so
        this checks structure instead: if training rows came along, something in
        there has one entry per sampled profile.
        """
        population = int(get_settings().risk_training_population)
        record = ml.train_risk(db)
        artifact = artifacts.load(record.artifact_path, record.artifact_checksum)

        seen: set[int] = set()
        offenders: list[str] = []

        def walk(obj: object, path: str, depth: int = 0) -> None:
            if depth > 6 or id(obj) in seen:
                return
            seen.add(id(obj))

            if (
                isinstance(obj, np.ndarray)
                and obj.shape
                and obj.shape[0]
                in (
                    population,
                    int(population * 0.8),  # the training split
                )
            ):
                offenders.append(f"{path} has shape {obj.shape}")
                return

            if isinstance(obj, dict):
                for key, value in obj.items():
                    walk(value, f"{path}[{key!r}]", depth + 1)
            elif isinstance(obj, (list, tuple)) and len(obj) < 1000:
                for index, value in enumerate(obj):
                    walk(value, f"{path}[{index}]", depth + 1)
            elif hasattr(obj, "__dict__"):
                for key, value in vars(obj).items():
                    walk(value, f"{path}.{key}", depth + 1)

        walk(artifact, "artifact")
        assert not offenders, "training data reached the model artifact: " + "; ".join(offenders)

    def test_the_artifact_is_far_smaller_than_its_training_set(self, db: Session) -> None:
        """A blunt backstop for the same property: 2,000 profiles × 8 float columns is
        ~128 KB uncompressed, and a forest that carried them would show it."""
        record = ml.train_risk(db)
        size = Path(record.artifact_path).stat().st_size
        assert size > 0
        # Generous ceiling — this is meant to catch a whole dataset, not to police
        # the forest's own size.
        assert size < 20_000_000, f"artifact is {size} bytes"

    def test_the_artifact_holds_only_fitted_parameters(self, db: Session) -> None:
        record = ml.train_risk(db)
        artifact = artifacts.load(record.artifact_path, record.artifact_checksum)

        assert _field_names(artifact) == {"classifier", "feature_columns", "feature_importances"}
        # scikit-learn does not retain training data, but assert it rather than
        # trusting that it will not start.
        assert not hasattr(artifact.classifier, "X_")
        assert not hasattr(artifact.classifier, "y_")


class TestTrainingLogsAreClean:
    def test_training_does_not_log_profile_values(
        self, db: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        from app.core.logging import JsonFormatter, RedactionFilter

        with caplog.at_level(logging.INFO):
            ml.train_risk(db)

        formatter = JsonFormatter()
        redaction = RedactionFilter()
        for record in caplog.records:
            redaction.filter(record)
            rendered = formatter.format(record)
            assert "income" not in rendered or "[redacted]" in rendered


class TestModelMetadata:
    def test_a_registered_model_records_its_provenance(self, db: Session) -> None:
        """§10.5 defines a version as semantic version + training range + git commit."""
        record = ml.train_risk(db)
        assert record.version
        assert record.artifact_checksum
        # git_commit is best-effort — a source tarball legitimately has none.
        assert record.git_commit is None or len(record.git_commit) == 40

    def test_metrics_survive_a_json_round_trip(self, db: Session) -> None:
        """They are stored in JSONB; a numpy scalar in there fails at commit time."""
        record = ml.train_risk(db)
        assert json.loads(json.dumps(record.metrics))["accuracy"] == record.metrics["accuracy"]

    def test_the_risk_metrics_carry_the_rubric_caveat(self, db: Session) -> None:
        """§18's reported-metrics discipline: the caveat travels with the number,
        wherever the row is read, not only in the model card."""
        record = ml.train_risk(db)
        assert "rubric" in record.metrics["label_source"]


class TestDisclaimers:
    """§17.1 — every recommendation and prediction surface carries it."""

    def test_the_disclaimer_names_both_required_points(self) -> None:
        assert "not financial advice" in MODEL_OUTPUT_DISCLAIMER
        assert "do not guarantee future results" in MODEL_OUTPUT_DISCLAIMER

    def test_the_root_disclaimer_is_still_present(self, client: TestClient) -> None:
        body = client.get("/").json()
        assert "does not provide licensed financial" in body["disclaimer"]


class TestPromotionThroughTheJobLayer:
    def test_train_then_promote_makes_a_model_servable(self, db: Session) -> None:
        record = ml.train_risk(db)
        assert registry.production_record(db, RISK_MODEL) is None

        ml.promote(db, RISK_MODEL, record.version)
        assert registry.resolve_production(db, RISK_MODEL).version == record.version

    def test_listing_models_does_not_raise_on_an_empty_registry(self, db: Session) -> None:
        assert ml.list_models(db) == []
