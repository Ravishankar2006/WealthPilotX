"""Risk assessment results (PRD §12 `risk_assessments`, FR-03).

Append-only. `GET /risk/latest` reads the newest row, and keeping the history is
what lets a user see that their risk class moved when their profile did — plus the
audit trail §10.5 wants sitting behind a served result.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Enum, ForeignKey, Index, Numeric, String, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_column, uuid_pk
from app.models.enums import RiskCategory


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (
        # What GET /risk/latest reads.
        Index("ix_risk_assessments_user_created_desc", "user_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # §10.5 — every served result names the model that produced it.
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)

    risk_score: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    risk_category: Mapped[RiskCategory] = mapped_column(
        Enum(RiskCategory, name="risk_category"), nullable=False
    )

    # FR-03's "main factors influencing the classification".
    top_factors: Mapped[list[dict[str, Any]]] = mapped_column(postgresql.JSONB, nullable=False)

    created_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:
        # Never interpolate the score or factors — reprs reach logs, and these are
        # derived from financial profile data (§11.2).
        return f"<RiskAssessment user={self.user_id}>"
