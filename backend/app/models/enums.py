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
