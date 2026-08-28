"""Model registry. Import every model here so Alembic's autogenerate sees them."""

from app.db.base import Base
from app.models.asset import Asset
from app.models.economic_indicator import EconomicIndicator
from app.models.enums import (
    AssetClass,
    AssetType,
    EconomicSeries,
    FinancialLiteracy,
    IngestionStatus,
    InvestmentExperience,
    InvestmentGoal,
    ModelStatus,
    RiskAppetite,
    RiskCategory,
    TrendDirection,
)
from app.models.financial_profile import FinancialProfile
from app.models.ingestion_run import IngestionRun
from app.models.market_data import MarketData
from app.models.model_record import ModelRecord
from app.models.prediction import Prediction
from app.models.refresh_token import RefreshToken
from app.models.risk_assessment import RiskAssessment
from app.models.user import User

__all__ = [
    "Asset",
    "AssetClass",
    "AssetType",
    "Base",
    "EconomicIndicator",
    "EconomicSeries",
    "FinancialLiteracy",
    "FinancialProfile",
    "IngestionRun",
    "IngestionStatus",
    "InvestmentExperience",
    "InvestmentGoal",
    "MarketData",
    "ModelRecord",
    "ModelStatus",
    "Prediction",
    "RefreshToken",
    "RiskAppetite",
    "RiskAssessment",
    "RiskCategory",
    "TrendDirection",
    "User",
]
