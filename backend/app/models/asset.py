"""The tracked asset universe (PRD §12 `assets`)."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_column, uuid_pk
from app.models.enums import AssetClass, AssetType

if TYPE_CHECKING:
    from app.models.market_data import MarketData


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(200))

    asset_type: Mapped[AssetType] = mapped_column(
        Enum(AssetType, name="asset_type"), nullable=False
    )
    asset_class: Mapped[AssetClass] = mapped_column(
        Enum(AssetClass, name="asset_class"), nullable=False, index=True
    )

    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    exchange: Mapped[str | None] = mapped_column(String(50))

    # Retiring a symbol stops ingestion without deleting its history — M3 still
    # needs the past of an asset we no longer track.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    created_at: Mapped[datetime] = created_at_column()

    bars: Mapped[list["MarketData"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Asset {self.symbol}>"
