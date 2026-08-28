import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    FinancialLiteracy,
    InvestmentExperience,
    InvestmentGoal,
    RiskAppetite,
)

MIN_AGE = 18
MAX_AGE = 120


class FinancialProfileIn(BaseModel):
    """FR-02. Ranges here are the first line of defence; the database carries
    matching CHECK constraints so a bad write cannot land through another path."""

    age: int = Field(ge=MIN_AGE, le=MAX_AGE)
    income: Decimal = Field(ge=0, le=Decimal("1e12"), decimal_places=2)
    savings: Decimal = Field(ge=0, le=Decimal("1e12"), decimal_places=2)
    risk_appetite: RiskAppetite
    investment_goal: InvestmentGoal
    investment_horizon: int = Field(ge=1, le=80, description="Years")
    experience: InvestmentExperience
    financial_literacy: FinancialLiteracy


class FinancialProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    age: int
    income: Decimal
    savings: Decimal
    risk_appetite: RiskAppetite
    investment_goal: InvestmentGoal
    investment_horizon: int
    experience: InvestmentExperience
    financial_literacy: FinancialLiteracy
    created_at: datetime
    updated_at: datetime


class ProfileCompleteness(BaseModel):
    """Consumed by FR-03 in Milestone 3 to block risk assessment on an
    incomplete profile, and by the frontend to drive onboarding state."""

    complete: bool
    missing_fields: list[str]
