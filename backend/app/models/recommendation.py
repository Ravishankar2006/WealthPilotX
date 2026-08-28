"""Per-asset recommendations (PRD §12 `recommendations`, FR-13)."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_column, uuid_pk


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Not in §12. Ties a reason to the portfolio it justifies, which is what
    # GET /recommendation/{id}/explanation needs to answer "explain this holding" —
    # a reason detached from its portfolio explains nothing in particular.
    portfolio_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        index=True,
    )

    score: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)

    # FR-13: never null. A recommendation without a reason must not be storable,
    # let alone servable.
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    portfolio: Mapped["object"] = relationship("Portfolio", viewonly=True)

    def __repr__(self) -> str:
        return f"<Recommendation user={self.user_id} asset={self.asset_id}>"
