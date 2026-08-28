"""Portfolio and recommendation response shapes (FR-10 to FR-13)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import AssetClass, RiskCategory
from app.schemas.risk import MODEL_OUTPUT_DISCLAIMER


class HoldingOut(BaseModel):
    symbol: str
    name: str | None = None
    asset_class: AssetClass
    weight: Decimal
    # FR-13 — every holding arrives with its reason, so no caller can render a
    # recommendation without one.
    reason: str | None = None
    recommendation_id: uuid.UUID | None = None


class PortfolioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    risk_category: RiskCategory
    expected_return: Decimal
    expected_risk: Decimal
    model_version: str
    created_at: datetime

    holdings: list[HoldingOut] = []
    # The constraint set actually in force, so "why only 12% equities?" is
    # answerable from the response rather than from the source.
    objective: dict[str, Any] | None = None
    explanation: str | None = None

    disclaimer: str = MODEL_OUTPUT_DISCLAIMER


class PortfolioListResponse(BaseModel):
    """The §13.1 list envelope."""

    data: list[PortfolioOut]
    next_cursor: str | None = None


class ExplanationOut(BaseModel):
    """FR-13 — `GET /recommendation/{id}/explanation`."""

    recommendation_id: uuid.UUID
    symbol: str
    score: Decimal
    reason: str
    model_version: str
    portfolio_id: uuid.UUID | None = None
    weight: Decimal | None = None
    portfolio_explanation: str | None = None
    created_at: datetime

    disclaimer: str = MODEL_OUTPUT_DISCLAIMER


class BacktestMetrics(BaseModel):
    """§19's five metrics, for the portfolio and its benchmark alike."""

    total_return: float
    annualised_return: float
    volatility: float
    sharpe_ratio: float
    max_drawdown: float


class BacktestOut(BaseModel):
    start: str
    end: str
    rebalances: int
    portfolio: BacktestMetrics
    benchmark: BacktestMetrics
    benchmark_symbol: str
    # §19 requires the cost assumption to be reported, not merely applied.
    transaction_cost_bps: float
    total_costs: float
    disclaimer: str = MODEL_OUTPUT_DISCLAIMER
