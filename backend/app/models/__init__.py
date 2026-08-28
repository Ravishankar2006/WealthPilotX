"""Model registry. Import every model here so Alembic's autogenerate sees them."""

from app.db.base import Base
from app.models.enums import (
    FinancialLiteracy,
    InvestmentExperience,
    InvestmentGoal,
    RiskAppetite,
    RiskCategory,
)
from app.models.financial_profile import FinancialProfile
from app.models.refresh_token import RefreshToken
from app.models.user import User

__all__ = [
    "Base",
    "FinancialLiteracy",
    "FinancialProfile",
    "InvestmentExperience",
    "InvestmentGoal",
    "RefreshToken",
    "RiskAppetite",
    "RiskCategory",
    "User",
]
