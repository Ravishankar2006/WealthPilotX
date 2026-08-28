"""Daily OHLCV (PRD §12 `market_data`, FR-04)."""

import uuid
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_column

if TYPE_CHECKING:
    from app.models.asset import Asset

# 18,6 rather than float: M3 computes returns off these, and float error compounds
# through that arithmetic. Six decimals covers sub-cent adjusted closes.
PRICE = Numeric(18, 6)


class MarketData(Base):
    __tablename__ = "market_data"
    __table_args__ = (
        # The database guarantee behind FR-06's "zero duplicate (asset_id, date)
        # rows", and the conflict target the ingestion upsert relies on.
        UniqueConstraint("asset_id", "date", name="uq_market_data_asset_date"),
        CheckConstraint(
            "open > 0 AND high > 0 AND low > 0 AND close > 0 AND adj_close > 0",
            name="ck_market_data_prices_positive",
        ),
        CheckConstraint("high >= low", name="ck_market_data_high_ge_low"),
        CheckConstraint("volume >= 0", name="ck_market_data_volume_non_negative"),
        # The access pattern for both the API (newest bars for one symbol) and M3's
        # feature windows. Declared here, not only in the migration, so the models
        # stay the single description of the schema.
        Index("ix_market_data_asset_date_desc", "asset_id", text("date DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)

    open: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    high: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    low: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    close: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    adj_close: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Which provider produced this row (§7.3). Makes "did the data change when we
    # swapped providers?" a query rather than an archaeology exercise.
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    ingested_at: Mapped[datetime] = created_at_column()

    asset: Mapped["Asset"] = relationship(back_populates="bars")

    def __repr__(self) -> str:
        return f"<MarketData asset={self.asset_id} date={self.date}>"
