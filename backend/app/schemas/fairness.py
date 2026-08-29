"""FR-14 response shapes.

Every metric is nullable, and that is the contract rather than an oversight: a
suppressed group carries `size` and `suppressed: true` with nothing else, so a
client that renders `0` for a missing number is visibly wrong rather than quietly
wrong.
"""

from pydantic import BaseModel

# §17.1 applies here too. The report describes how model outputs are distributed,
# which is a model-output surface, and a disparity ratio is exactly the kind of
# number that gets screenshotted without its context.
FAIRNESS_DISCLAIMER = (
    "These are aggregate statistics over this instance's own users, published for "
    "auditing. They describe how an educational model's outputs are distributed; "
    "they are not a compliance assessment and not financial advice."
)


class GroupStatsOut(BaseModel):
    group: str
    size: int
    suppressed: bool
    risk_distribution: dict[str, float] | None = None
    mean_risk_score: float | None = None
    mean_equity_weight: float | None = None
    portfolio_rate: float | None = None


class DisparityOut(BaseModel):
    metric: str
    ratio: float
    lowest_group: str
    highest_group: str
    lowest_rate: float
    highest_rate: float
    flagged: bool


class DimensionReportOut(BaseModel):
    dimension: str
    label: str
    groups: list[GroupStatsOut]
    disparity: DisparityOut | None = None
    note: str | None = None


class FairnessReportOut(BaseModel):
    population: int
    reportable_population: int
    min_group_size: int
    dimensions: list[DimensionReportOut]
    disclaimer: str = FAIRNESS_DISCLAIMER
