"""M4 recommendation: portfolios, portfolio assets, recommendations

Hand-written per the standing rule from Phase 1 decision 3.

No new enum types this revision — `risk_category` already exists from M3, and
reusing it means `create_table` must be told not to emit a second CREATE TYPE.
That is what `postgresql.ENUM(..., create_type=False)` below is for; without it
the upgrade fails with "type risk_category already exists", which is the same
class of enum bug that broke the M1 revision from the other direction.

Revision ID: 17ee5a1fdd38
Revises: f1c7dc111b6d
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "17ee5a1fdd38"
down_revision: str | None = "f1c7dc111b6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RISK_CATEGORY = postgresql.ENUM("LOW", "MEDIUM", "HIGH", name="risk_category", create_type=False)


def upgrade() -> None:
    op.create_table(
        "portfolios",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("expected_return", sa.Numeric(10, 6), nullable=False),
        sa.Column("expected_risk", sa.Numeric(10, 6), nullable=False),
        sa.Column("risk_category", RISK_CATEGORY, nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False),
        sa.Column("objective", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_portfolios_user_id", "portfolios", ["user_id"])
    # What GET /portfolio/current and /history read.
    op.create_index(
        "ix_portfolios_user_created_desc",
        "portfolios",
        ["user_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "portfolio_assets",
        sa.Column("portfolio_id", sa.UUID(), nullable=False),
        sa.Column("asset_id", sa.UUID(), nullable=False),
        sa.Column("weight", sa.Numeric(9, 8), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("portfolio_id", "asset_id"),
        sa.CheckConstraint("weight >= 0 AND weight <= 1", name="ck_portfolio_asset_weight_range"),
    )

    # FR-11: weights sum to 1.0 ± 0.001. §12 says this is enforced at the application
    # layer, presumably because it spans rows — but a DEFERRABLE constraint trigger
    # can hold it in the database too. The application check gives a good error
    # early; this makes a malformed portfolio genuinely unstorable, including by a
    # future code path that forgets to check.
    #
    # Duplicated verbatim in app/models/portfolio.py, which attaches it to
    # `after_create` so `Base.metadata.create_all` emits it too — otherwise the test
    # database would lack a guarantee the migrated database has. A migration must
    # not import application code, so the copy is deliberate. **Change both
    # together.**
    op.execute(
        """
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
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER portfolio_weights_sum_to_one
        AFTER INSERT OR UPDATE OR DELETE ON portfolio_assets
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION check_portfolio_weights_sum();
        """
    )

    op.create_table(
        "recommendations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("asset_id", sa.UUID(), nullable=False),
        sa.Column("portfolio_id", sa.UUID(), nullable=True),
        sa.Column("score", sa.Numeric(9, 6), nullable=False),
        # FR-13: a recommendation without a reason must not be storable.
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recommendations_user_id", "recommendations", ["user_id"])
    op.create_index("ix_recommendations_asset_id", "recommendations", ["asset_id"])
    op.create_index("ix_recommendations_portfolio_id", "recommendations", ["portfolio_id"])


def downgrade() -> None:
    op.drop_table("recommendations")
    op.execute("DROP TRIGGER IF EXISTS portfolio_weights_sum_to_one ON portfolio_assets")
    op.execute("DROP FUNCTION IF EXISTS check_portfolio_weights_sum()")
    op.drop_table("portfolio_assets")
    op.drop_table("portfolios")
    # risk_category is NOT dropped here — it belongs to the M3 revision, which
    # created it and is responsible for removing it.
