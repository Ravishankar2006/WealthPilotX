"""Generated portfolios (PRD §12 `portfolios`, `portfolio_assets`).

Immutable once written (Phase 4 plan, decision 5). `POST /portfolio/generate` always
inserts; nothing updates a portfolio in place. An explanation has to still be true a
month later, and it cannot be if the thing it explains was edited underneath it.

A portfolio is a **recommendation, never a position.** Nothing here records what a
user owns, and nothing should — that is the first step toward the custody and
execution surfaces that are permanent non-goals (PRD §5).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    event,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_column, uuid_pk
from app.models.enums import RiskCategory

if TYPE_CHECKING:
    from app.models.asset import Asset


class Portfolio(Base):
    __tablename__ = "portfolios"
    __table_args__ = (Index("ix_portfolios_user_created_desc", "user_id", text("created_at DESC")),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    expected_return: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    expected_risk: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)

    # Not in §12's column list. The risk class the portfolio was built for, because
    # a portfolio generated when the user was MEDIUM means something different once
    # they have become HIGH — and §10.5 requires every recommendation to name what
    # produced it.
    risk_category: Mapped[RiskCategory] = mapped_column(
        Enum(RiskCategory, name="risk_category"), nullable=False
    )
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)

    # The λ, caps and μ source actually in force. "Why is this 12% and not 20%?" is
    # answerable only from the constraints that applied at generation time;
    # recomputing them from today's settings would answer a different question.
    objective: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB)

    created_at: Mapped[datetime] = created_at_column()

    holdings: Mapped[list["PortfolioAsset"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Portfolio user={self.user_id} {self.risk_category}>"


class PortfolioAsset(Base):
    __tablename__ = "portfolio_assets"
    __table_args__ = (
        CheckConstraint("weight >= 0 AND weight <= 1", name="ck_portfolio_asset_weight_range"),
    )

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        primary_key=True,
    )

    weight: Mapped[Decimal] = mapped_column(Numeric(9, 8), nullable=False)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="holdings")
    asset: Mapped["Asset"] = relationship()

    def __repr__(self) -> str:
        return f"<PortfolioAsset {self.asset_id} {self.weight}>"


# FR-11's weights-sum-to-1 rule, as a database guarantee.
#
# §12 says this is enforced at the application layer, presumably because it spans
# rows — but a DEFERRABLE constraint trigger holds it in the database too, so a
# future code path that forgets to check still cannot store a malformed portfolio.
#
# Emitted on `after_create` so `Base.metadata.create_all` installs it as well as the
# migration. Without this the test database (built by create_all) would lack a
# guarantee the migrated database has, and the suite would pass on schemas that
# production rejects — which is exactly what happened before this was added.
#
# The migration carries its own copy of this SQL on purpose: a migration must not
# import application code, because it has to keep describing the schema as it was
# when it was written. **Change both together.**
WEIGHT_SUM_DDL = """
CREATE OR REPLACE FUNCTION check_portfolio_weights_sum() RETURNS trigger AS $$
DECLARE
    target uuid := COALESCE(NEW.portfolio_id, OLD.portfolio_id);
    total numeric;
BEGIN
    SELECT COALESCE(SUM(weight), 0) INTO total
    FROM portfolio_assets WHERE portfolio_id = target;

    -- Zero rows is a portfolio mid-delete, which is legitimate.
    IF total <> 0 AND ABS(total - 1) > 0.001 THEN
        RAISE EXCEPTION
            'Portfolio % weights sum to %, outside 1.0 +/- 0.001 (FR-11)',
            target, total;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER portfolio_weights_sum_to_one
AFTER INSERT OR UPDATE OR DELETE ON portfolio_assets
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION check_portfolio_weights_sum();
"""


@event.listens_for(PortfolioAsset.__table__, "after_create")
def _install_weight_sum_trigger(target: Any, connection: Any, **kwargs: Any) -> None:
    """Install the trigger after the table is created.

    A plain callback rather than `DDL(...)`: the DDL construct runs its text through
    printf-style interpolation, and this body is full of `%` — both plpgsql's RAISE
    placeholders and the `:=` assignment — which turns into an unsupported-format
    error long before Postgres sees it. Escaping every one of them would make the
    SQL diverge from the migration's copy for no benefit.
    """
    if connection.dialect.name != "postgresql":
        return
    connection.execute(text(WEIGHT_SUM_DDL))
