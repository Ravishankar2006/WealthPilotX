"""The model registry table (PRD §12 `models`, §10.5).

Two columns are added to §12's list, both because the registry does not work
without them:

* `artifact_path` — §12 describes the metadata but names no way to reach the
  artifact it describes. A registry row that cannot find its model is a note.
* `git_commit` — §10.5 defines a version as "semantic version + training-data date
  range + git commit hash", and the table had nowhere to put the third part.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, Enum, String, Text, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_column, uuid_pk
from app.models.enums import ModelStatus

# Registered model names. Constants rather than free strings: inference resolves the
# production model by name, and a typo would surface as "no production model" at
# request time rather than at import.
RISK_MODEL = "risk_classifier"
PREDICTION_MODEL = "market_predictor"


class ModelRecord(Base):
    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_models_name_version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)

    # §12 specifies a daterange; stored as two columns because SQLAlchemy's range
    # support is dialect-specific and two dates are trivially queryable.
    training_start: Mapped[date | None] = mapped_column(Date)
    training_end: Mapped[date | None] = mapped_column(Date)

    metrics: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB)
    status: Mapped[ModelStatus] = mapped_column(
        Enum(ModelStatus, name="model_status"), nullable=False, index=True
    )

    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    # Detects an artifact replaced underneath its row — a stale file on a shared
    # volume otherwise serves predictions the metrics never described.
    artifact_checksum: Mapped[str | None] = mapped_column(String(64))
    git_commit: Mapped[str | None] = mapped_column(String(40))

    created_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:
        return f"<ModelRecord {self.name}:{self.version} {self.status}>"
