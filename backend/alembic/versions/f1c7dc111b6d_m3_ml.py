"""M3 ML: models registry, risk assessments, predictions

Hand-written, per the standing rule from Phase 1 decision 3. Three new enum types
are created and all three are dropped in `downgrade` — SQLAlchemy's `Enum` creates
a Postgres type as a side effect of `create_table` but drops nothing on
`drop_table`, which is how the M1 revision originally broke.

Revision ID: f1c7dc111b6d
Revises: c3914c27454e
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f1c7dc111b6d"
down_revision: str | None = "c3914c27454e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_TYPES = ("model_status", "risk_category", "trend_direction")

# Values duplicated from app.models.enums deliberately: a migration that imports
# application code stops describing the schema as it was when it was written.
MODEL_STATUS = sa.Enum("EXPERIMENT", "PRODUCTION", "RETIRED", name="model_status")
RISK_CATEGORY = sa.Enum("LOW", "MEDIUM", "HIGH", name="risk_category")
TREND_DIRECTION = sa.Enum("UP", "DOWN", "FLAT", name="trend_direction")


def upgrade() -> None:
    op.create_table(
        "models",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("training_start", sa.Date(), nullable=True),
        sa.Column("training_end", sa.Date(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", MODEL_STATUS, nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("artifact_checksum", sa.String(length=64), nullable=True),
        sa.Column("git_commit", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        # Stops two artifacts claiming one version.
        sa.UniqueConstraint("name", "version", name="uq_models_name_version"),
    )
    op.create_index("ix_models_name", "models", ["name"])
    op.create_index("ix_models_status", "models", ["status"])

    op.create_table(
        "risk_assessments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False),
        sa.Column("risk_score", sa.Numeric(6, 5), nullable=False),
        sa.Column("risk_category", RISK_CATEGORY, nullable=False),
        sa.Column("top_factors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_risk_assessments_user_id", "risk_assessments", ["user_id"])
    # What GET /risk/latest reads.
    op.create_index(
        "ix_risk_assessments_user_created_desc",
        "risk_assessments",
        ["user_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "predictions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("asset_id", sa.UUID(), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False),
        sa.Column("prediction_date", sa.Date(), nullable=False),
        sa.Column("predicted_return", sa.Numeric(12, 8), nullable=False),
        sa.Column("trend", TREND_DIRECTION, nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False, server_default="20"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Includes model_version so a retrain writes a new row rather than
        # overwriting the prediction that explains an existing recommendation.
        sa.UniqueConstraint(
            "asset_id", "prediction_date", "model_version", name="uq_prediction_asset_date_model"
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_prediction_confidence"),
    )
    op.create_index("ix_predictions_asset_id", "predictions", ["asset_id"])
    op.create_index("ix_predictions_prediction_date", "predictions", ["prediction_date"])
    op.create_index(
        "ix_predictions_asset_date_desc",
        "predictions",
        ["asset_id", sa.text("prediction_date DESC")],
    )


def downgrade() -> None:
    op.drop_table("predictions")
    op.drop_table("risk_assessments")
    op.drop_table("models")

    for name in ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {name}")
