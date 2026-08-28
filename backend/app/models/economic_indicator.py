"""Macroeconomic observations (PRD §12 `economic_indicators`, FR-05)."""

from datetime import date as date_type
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, Enum, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_column
from app.models.enums import EconomicSeries


class EconomicIndicator(Base):
    __tablename__ = "economic_indicators"
    __table_args__ = (
        # FRED revises published figures. The unique constraint plus an upsert means
        # a revision replaces the old value in place rather than accumulating two
        # rows for one as-of date, which would quietly double-count in M3.
        UniqueConstraint("series", "date", name="uq_economic_indicator_series_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    series: Mapped[EconomicSeries] = mapped_column(
        Enum(EconomicSeries, name="economic_series"), nullable=False, index=True
    )
    date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)

    source: Mapped[str] = mapped_column(String(50), nullable=False)
    ingested_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:
        return f"<EconomicIndicator {self.series} {self.date}>"
