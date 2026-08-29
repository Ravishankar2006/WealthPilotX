"""M6 hardening: the model_monitoring table

Hand-finished from autogenerate, per the standing rule from Phase 1 decision 3.
Two things autogenerate does not get right on its own:

* The composite index is `(model_name, created_at DESC)`. Autogenerate did render
  the descending part, but as `sa.literal_column`, where the other three migrations
  write `sa.text` — normalised so all four read the same way.
* `drop_table` does not drop the enum types `create_table` implicitly created. The
  M1 revision shipped with exactly that gap and an upgrade-only CI check walked
  straight past it; CI now runs downgrade-then-upgrade, which is what makes the
  explicit DROP TYPE below load-bearing rather than tidy.

Revision ID: f7096e9ecdf9
Revises: 17ee5a1fdd38
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f7096e9ecdf9"
down_revision: str | None = "17ee5a1fdd38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_TYPES = ("drift_check", "drift_verdict")


def upgrade() -> None:
    op.create_table(
        "model_monitoring",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False),
        sa.Column(
            "check",
            sa.Enum("FEATURE_STABILITY", "PREDICTION_ERROR", name="drift_check"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=100), nullable=False),
        # Nullable: an INSUFFICIENT_DATA row records that no measurement could be
        # taken. A 0.0 there would be indistinguishable from a perfectly stable
        # feature, which is the opposite conclusion.
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column(
            "verdict",
            sa.Enum("STABLE", "WATCH", "ALERT", "INSUFFICIENT_DATA", name="drift_verdict"),
            nullable=False,
        ),
        sa.Column("reference_start", sa.Date(), nullable=True),
        sa.Column("reference_end", sa.Date(), nullable=True),
        sa.Column("window_start", sa.Date(), nullable=True),
        sa.Column("window_end", sa.Date(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_model_monitoring_check"), "model_monitoring", ["check"], unique=False)
    # The read pattern: the newest observations for one model.
    op.create_index(
        "ix_model_monitoring_model_created_desc",
        "model_monitoring",
        ["model_name", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        op.f("ix_model_monitoring_model_name"), "model_monitoring", ["model_name"], unique=False
    )
    op.create_index(
        op.f("ix_model_monitoring_verdict"), "model_monitoring", ["verdict"], unique=False
    )


def downgrade() -> None:
    op.drop_table("model_monitoring")

    for name in ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {name}")
