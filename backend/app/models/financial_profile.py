import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.crypto import EncryptedNumeric
from app.db.base import Base, created_at_column, uuid_pk
from app.models.enums import (
    FinancialLiteracy,
    InvestmentExperience,
    InvestmentGoal,
    RiskAppetite,
)

if TYPE_CHECKING:
    from app.models.user import User


class FinancialProfile(Base):
    __tablename__ = "financial_profiles"
    __table_args__ = (
        CheckConstraint("age >= 18 AND age <= 120", name="ck_profile_age_range"),
        CheckConstraint("investment_horizon >= 1", name="ck_profile_horizon_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    age: Mapped[int] = mapped_column(Integer, nullable=False)

    # Encrypted at the application layer — see app/core/crypto.py. Not queryable,
    # not aggregatable in SQL, by design.
    income: Mapped[Decimal] = mapped_column(EncryptedNumeric, nullable=False)
    savings: Mapped[Decimal] = mapped_column(EncryptedNumeric, nullable=False)

    risk_appetite: Mapped[RiskAppetite] = mapped_column(
        Enum(RiskAppetite, name="risk_appetite"), nullable=False
    )
    investment_goal: Mapped[InvestmentGoal] = mapped_column(
        Enum(InvestmentGoal, name="investment_goal"), nullable=False
    )
    investment_horizon: Mapped[int] = mapped_column(Integer, nullable=False)
    experience: Mapped[InvestmentExperience] = mapped_column(
        Enum(InvestmentExperience, name="investment_experience"), nullable=False
    )
    financial_literacy: Mapped[FinancialLiteracy] = mapped_column(
        Enum(FinancialLiteracy, name="financial_literacy"), nullable=False
    )

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="profile")

    def __repr__(self) -> str:
        # Never interpolate income or savings into a repr — reprs reach logs.
        return f"<FinancialProfile user={self.user_id}>"
