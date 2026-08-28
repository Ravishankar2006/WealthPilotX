"""The risk-scoring rubric (Phase 3 plan, decision 1 and §5).

**Read this before reading the model.** There is no labelled dataset of real users'
risk classes, so the Random Forest in `model.py` is trained on profiles labelled by
the rule below. That makes the model a learned approximation of this function — its
accuracy measures fidelity to this rubric, not correctness about real people.

The consequence, stated plainly because PRD §18 exists to stop it being glossed
over: a high accuracy score here is unsurprising rather than impressive. The rubric
is the artifact that carries the domain judgment and deserves the review; the model
is what makes it servable with feature importances attached.

Two ideas the weights encode:

* **Willingness and capacity are different things.** Stated appetite is the only
  field that expresses a preference; the rest are proxies for capacity to absorb
  loss. Appetite carries the largest single weight so the rubric does not override
  what someone told us about themselves — a young, wealthy, self-described
  conservative investor lands in MEDIUM, not HIGH.
* **Time is the strongest capacity proxy.** A long horizon is what converts
  volatility from a loss into a fluctuation, so horizon outweighs age, income and
  savings individually.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.models.enums import (
    FinancialLiteracy,
    InvestmentExperience,
    RiskAppetite,
    RiskCategory,
)

# Weights sum to 1.0; asserted at import so an edit cannot silently rescale scores.
WEIGHT_APPETITE = 0.30
WEIGHT_HORIZON = 0.20
WEIGHT_AGE = 0.15
WEIGHT_SAVINGS_RATIO = 0.15
WEIGHT_EXPERIENCE = 0.10
WEIGHT_LITERACY = 0.10

_WEIGHTS = (
    WEIGHT_APPETITE,
    WEIGHT_HORIZON,
    WEIGHT_AGE,
    WEIGHT_SAVINGS_RATIO,
    WEIGHT_EXPERIENCE,
    WEIGHT_LITERACY,
)
assert abs(sum(_WEIGHTS) - 1.0) < 1e-9, "Rubric weights must sum to 1.0"

# Category boundaries: LOW < 0.40 <= MEDIUM <= 0.70 < HIGH.
LOW_THRESHOLD = 0.40
HIGH_THRESHOLD = 0.70

# Saturation points. Beyond these, more of the thing stops adding capacity: a
# 40-year horizon is not twice as tolerant as a 20-year one, and a household with
# 3x its income saved is already insulated from ordinary drawdowns.
HORIZON_SATURATION_YEARS = 25
SAVINGS_RATIO_SATURATION = 3.0
AGE_FLOOR = 18
AGE_CEILING = 75

# Stands in for the denominator when income is zero, so a buffer is still measured
# against something rather than against nothing. Roughly a median income — the exact
# figure matters far less than not dividing by zero or declaring the ratio infinite.
NOMINAL_INCOME_WHEN_UNEMPLOYED = Decimal("50000")

APPETITE_SCORES = {
    RiskAppetite.CONSERVATIVE: 0.0,
    RiskAppetite.MODERATE: 0.5,
    RiskAppetite.AGGRESSIVE: 1.0,
}

EXPERIENCE_SCORES = {
    InvestmentExperience.NONE: 0.0,
    InvestmentExperience.BEGINNER: 0.33,
    InvestmentExperience.INTERMEDIATE: 0.67,
    InvestmentExperience.ADVANCED: 1.0,
}

LITERACY_SCORES = {
    FinancialLiteracy.LOW: 0.0,
    FinancialLiteracy.MEDIUM: 0.5,
    FinancialLiteracy.HIGH: 1.0,
}

# Human-readable names for the top-factor strings FR-03 requires.
FACTOR_LABELS = {
    "appetite": "stated risk appetite",
    "horizon": "investment horizon",
    "age": "age",
    "savings_ratio": "savings relative to income",
    "experience": "investment experience",
    "literacy": "financial literacy",
}


@dataclass(frozen=True, slots=True)
class RubricComponents:
    """Each component's normalised [0, 1] value, before weighting."""

    appetite: float
    horizon: float
    age: float
    savings_ratio: float
    experience: float
    literacy: float

    def as_dict(self) -> dict[str, float]:
        return {
            "appetite": self.appetite,
            "horizon": self.horizon,
            "age": self.age,
            "savings_ratio": self.savings_ratio,
            "experience": self.experience,
            "literacy": self.literacy,
        }


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def horizon_component(years: int) -> float:
    """Longer horizon → more capacity, saturating at 25 years."""
    return _clamp(years / HORIZON_SATURATION_YEARS)


def age_component(age: int) -> float:
    """Younger → more capacity, over an 18–75 band.

    Inverted and clamped: an 18-year-old scores 1.0, anyone 75 or older scores 0.0.
    """
    return _clamp((AGE_CEILING - age) / (AGE_CEILING - AGE_FLOOR))


def savings_ratio_component(income: Decimal, savings: Decimal) -> float:
    """Savings as a multiple of annual income, saturating at 3x.

    Zero income is a real answer — a student, or someone between jobs — and must not
    divide by zero. The first version of this rule returned 1.0 for anyone with no
    income and any savings at all, on the reasoning that their savings were infinite
    relative to their income. That was wrong, and wrong in the direction that does
    real harm: a 21-year-old with no income and 2,000 saved scored the *maximum*
    buffer component and came out HIGH risk capacity overall. Someone with no income
    and almost no savings is the most precarious case there is, not the safest.

    With no income the ratio is undefined rather than infinite, so the buffer is
    measured against a nominal income instead. That keeps the same 3x saturation as
    everyone else and gives a small buffer a small score.
    """
    reference = income if income > 0 else NOMINAL_INCOME_WHEN_UNEMPLOYED
    return _clamp(float(savings / reference) / SAVINGS_RATIO_SATURATION)


def components(
    *,
    age: int,
    income: Decimal,
    savings: Decimal,
    risk_appetite: RiskAppetite,
    investment_horizon: int,
    experience: InvestmentExperience,
    financial_literacy: FinancialLiteracy,
) -> RubricComponents:
    return RubricComponents(
        appetite=APPETITE_SCORES[RiskAppetite(risk_appetite)],
        horizon=horizon_component(investment_horizon),
        age=age_component(age),
        savings_ratio=savings_ratio_component(income, savings),
        experience=EXPERIENCE_SCORES[InvestmentExperience(experience)],
        literacy=LITERACY_SCORES[FinancialLiteracy(financial_literacy)],
    )


def score(parts: RubricComponents) -> float:
    """The weighted risk score, in [0, 1]."""
    return (
        WEIGHT_APPETITE * parts.appetite
        + WEIGHT_HORIZON * parts.horizon
        + WEIGHT_AGE * parts.age
        + WEIGHT_SAVINGS_RATIO * parts.savings_ratio
        + WEIGHT_EXPERIENCE * parts.experience
        + WEIGHT_LITERACY * parts.literacy
    )


def categorise(value: float) -> RiskCategory:
    if value < LOW_THRESHOLD:
        return RiskCategory.LOW
    if value > HIGH_THRESHOLD:
        return RiskCategory.HIGH
    return RiskCategory.MEDIUM


def weighted_contributions(parts: RubricComponents) -> dict[str, float]:
    """Each component's contribution to the score — its normalised value times its
    weight. This is what makes "why this category?" answerable per user, as opposed
    to the model's global feature importances, which are the same for everyone."""
    return {
        "appetite": WEIGHT_APPETITE * parts.appetite,
        "horizon": WEIGHT_HORIZON * parts.horizon,
        "age": WEIGHT_AGE * parts.age,
        "savings_ratio": WEIGHT_SAVINGS_RATIO * parts.savings_ratio,
        "experience": WEIGHT_EXPERIENCE * parts.experience,
        "literacy": WEIGHT_LITERACY * parts.literacy,
    }
