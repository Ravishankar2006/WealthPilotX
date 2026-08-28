"""Market predictions (PRD §12 `predictions`, FR-08)."""

import uuid
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_column, uuid_pk
from app.models.enums import TrendDirection


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        # Keyed on the model version as well as the date, so a retrained model writes
        # a new row rather than overwriting the prediction that explains an existing
        # recommendation. Re-running the job for one model stays idempotent.
        UniqueConstraint(
            "asset_id", "prediction_date", "model_version", name="uq_prediction_asset_date_model"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_prediction_confidence"),
        # The lookup behind GET /market/{symbol}/prediction.
        Index("ix_predictions_asset_date_desc", "asset_id", text("prediction_date DESC")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    asset_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    prediction_date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)

    # The expected log return over the model's horizon.
    predicted_return: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    trend: Mapped[TrendDirection] = mapped_column(
        Enum(TrendDirection, name="trend_direction"), nullable=False
    )
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)

    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=20)

    created_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:
        return f"<Prediction asset={self.asset_id} date={self.prediction_date}>"
