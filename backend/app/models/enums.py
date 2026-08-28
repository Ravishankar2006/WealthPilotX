"""Closed vocabularies from PRD §12. Stored as native Postgres enums."""

import enum


class RiskAppetite(enum.StrEnum):
    CONSERVATIVE = "CONSERVATIVE"
    MODERATE = "MODERATE"
    AGGRESSIVE = "AGGRESSIVE"


class InvestmentGoal(enum.StrEnum):
    RETIREMENT = "RETIREMENT"
    GROWTH = "GROWTH"
    WEALTH_CREATION = "WEALTH_CREATION"


class InvestmentExperience(enum.StrEnum):
    NONE = "NONE"
    BEGINNER = "BEGINNER"
    INTERMEDIATE = "INTERMEDIATE"
    ADVANCED = "ADVANCED"


class FinancialLiteracy(enum.StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RiskCategory(enum.StrEnum):
    """Not used until FR-03 in Milestone 3; defined here so the vocabulary is
    settled in one place before two modules invent competing versions."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AssetType(enum.StrEnum):
    """The instrument, per PRD §12's `assets.asset_type`."""

    EQUITY = "EQUITY"
    ETF = "ETF"
    BOND = "BOND"
    COMMODITY = "COMMODITY"
    INDEX = "INDEX"


class AssetClass(enum.StrEnum):
    """The economic exposure, which is not the same thing as the instrument.

    An aggregate-bond ETF is an `ETF` by type and a `BOND` by class. FR-11 needs to
    cap "maximum weights by asset class", so the optimizer needs the exposure; using
    `asset_type` for both would force a hard-coded symbol lookup in M4, which is the
    static mapping FR-10 rules out.
    """

    EQUITY = "EQUITY"
    BOND = "BOND"
    COMMODITY = "COMMODITY"
    REAL_ESTATE = "REAL_ESTATE"
    CASH = "CASH"


class EconomicSeries(enum.StrEnum):
    """The five series FR-05 requires."""

    INFLATION = "INFLATION"
    INTEREST_RATE = "INTEREST_RATE"
    GDP = "GDP"
    UNEMPLOYMENT = "UNEMPLOYMENT"
    FX_RATE = "FX_RATE"


class IngestionStatus(enum.StrEnum):
    """PARTIAL is deliberately distinct from SUCCESS: FR-04 forbids treating "most
    symbols worked" as a clean run, because the gap it leaves is invisible to M3."""

    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ModelStatus(enum.StrEnum):
    """Model lifecycle (§10.5). A model serves traffic only in PRODUCTION."""

    EXPERIMENT = "EXPERIMENT"
    PRODUCTION = "PRODUCTION"
    RETIRED = "RETIRED"


class TrendDirection(enum.StrEnum):
    """FR-08's trend output.

    FLAT exists so the model can decline to call a direction. Without it, a
    predicted return of +0.02% would be reported as "UP" with the same vocabulary
    as a predicted 8% rally, which overstates what the model actually said.
    """

    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"
