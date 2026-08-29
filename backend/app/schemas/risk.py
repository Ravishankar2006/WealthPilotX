"""Risk and prediction response shapes (FR-03, FR-08, FR-09)."""

import uuid
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import RiskCategory, TrendDirection

# §17.1 requires this on every recommendation and prediction surface. It travels in
# the payload, not only in the UI, so an API consumer cannot present a model output
# without it having been supplied.
MODEL_OUTPUT_DISCLAIMER = (
    "This is an educational model output, not financial advice. Past performance and "
    "model predictions do not guarantee future results."
)


class RiskFactor(BaseModel):
    factor: str
    contribution: float
    detail: str


class RiskAssessmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    risk_category: RiskCategory
    risk_score: Decimal
    top_factors: list[RiskFactor]
    model_version: str
    created_at: datetime
    disclaimer: str = MODEL_OUTPUT_DISCLAIMER


class IncompleteProfile(BaseModel):
    """FR-02's acceptance criterion: block the request and list what is missing."""

    missing_fields: list[str]


class PredictionOut(BaseModel):
    """FR-08 plus FR-09's six asset-analysis metrics.

    FR-09 requires each metric to be "returned or explicitly marked unavailable with
    a reason", so every metric is nullable and `unavailable` carries the reason.
    """

    symbol: str
    prediction_date: date_type
    horizon_days: int

    # FR-08
    predicted_return: Decimal
    trend: TrendDirection
    confidence: Decimal
    model_version: str

    # FR-09's remaining metrics
    expected_return: Decimal | None = None
    volatility: float | None = None
    momentum: float | None = None
    risk_score: float | None = None

    unavailable: list[str] = []
    disclaimer: str = MODEL_OUTPUT_DISCLAIMER


class FeatureContributionOut(BaseModel):
    """One feature's Shapley contribution to a prediction (FR-13, advanced).

    `contribution` is in the units of the target — a 20-day log return — so the
    numbers are directly comparable with `predicted_return` rather than being an
    abstract importance score.
    """

    feature: str
    label: str
    value: float | None
    contribution: float
    direction: str


class PredictionExplanationOut(BaseModel):
    """FR-13's advanced explanation for a stored market prediction.

    `base_value + sum(contributions) == predicted_return` over the *full* feature
    set. `contributions` here is the truncated view, so the identity will not close
    on this payload — `contributions_shown` and `contributions_total` say so
    explicitly rather than leaving a reader to wonder why the arithmetic misses.
    """

    symbol: str
    prediction_date: date_type
    horizon_days: int
    model_version: str

    predicted_return: float
    base_value: float
    contributions: list[FeatureContributionOut]
    contributions_shown: int
    contributions_total: int

    # False when the model version that made this prediction no longer reproduces
    # it from the same inputs. The explanation is still served — it is a genuine
    # decomposition of what the model does now — but a reader deserves to know the
    # number moved. See `services/explanation_service.py`.
    reproduced: bool = True

    disclaimer: str = MODEL_OUTPUT_DISCLAIMER
