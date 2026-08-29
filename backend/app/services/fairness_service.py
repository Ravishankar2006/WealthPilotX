"""FR-14 — group-level outcome statistics for auditing.

What this module is for, and what it is not for. §9 FR-14 is explicit: the grouping
attributes exist "for auditing and evaluation rather than using protected attributes
as investment decision inputs". Nothing here feeds a recommendation. It reads
outcomes that have already been produced and asks whether they land differently on
different groups.

Three rules shape the implementation:

1. **Suppression happens here, not in the UI.** §11.2 sets a minimum group size of
   20. A group below it is returned with `suppressed=True` and every metric `None`.
   Filtering in the response layer would mean the numbers still crossed a process
   boundary and still reached a log on the way; filtering in the presentation layer
   would mean they reached the browser.

2. **Suppressed is not zero.** A group of three users whose HIGH-risk rate is
   returned as `0.0` reads as a measured finding. It is not one, and the difference
   matters most on exactly the small groups where re-identification is a risk.

3. **Raw income never leaves `_band_income`.** Income is encrypted at rest and
   decrypted by the ORM on read, so aggregating it means holding plaintext financial
   PII in this process. It is converted to a band immediately and the value is not
   returned, logged, or kept.

A note on interpretation, which belongs next to the code rather than only in the UI:
age and income *are* inputs to the risk rubric, by design and by FR-02. Disparity
across those bands is therefore expected and is not by itself evidence of anything
wrong. The metric worth reading is whether the disparity is larger than the rubric's
declared weighting can account for, and whether it also appears across literacy and
experience, which the product treats as descriptive rather than as capacity.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.enums import AssetClass, FinancialLiteracy, InvestmentExperience, RiskCategory
from app.models.financial_profile import FinancialProfile
from app.models.portfolio import Portfolio, PortfolioAsset
from app.models.risk_assessment import RiskAssessment

# §11.2: "only reported at group sizes above a minimum threshold (e.g., n ≥ 20) to
# prevent re-identification". Taken as a hard floor rather than a default, because
# the whole point of the threshold is that it is not negotiable per report.
MIN_GROUP_SIZE = 20

# The four-fifths rule: a selection rate below 80% of the highest group's is the
# conventional threshold for adverse impact. Adopted as the flag level here — it is
# a screening convention, not a legal finding, and the report says so.
DISPARITY_FLAG_RATIO = 0.80

AGE_BANDS: tuple[tuple[str, int, int], ...] = (
    ("18-29", 18, 29),
    ("30-44", 30, 44),
    ("45-59", 45, 59),
    ("60+", 60, 200),
)

INCOME_BANDS: tuple[tuple[str, Decimal, Decimal], ...] = (
    ("under 50k", Decimal(0), Decimal(50_000)),
    ("50k-100k", Decimal(50_000), Decimal(100_000)),
    ("100k-200k", Decimal(100_000), Decimal(200_000)),
    ("200k+", Decimal(200_000), Decimal(10**12)),
)

DIMENSION_LABELS: dict[str, str] = {
    "age_band": "Age",
    "income_band": "Annual income",
    "financial_literacy": "Self-reported financial literacy",
    "experience": "Investment experience",
}

# Ordering for the categorical dimensions, so a report renders low-to-high rather
# than in whatever order the rows arrived.
DIMENSION_ORDER: dict[str, tuple[str, ...]] = {
    "age_band": tuple(name for name, _, _ in AGE_BANDS),
    "income_band": tuple(name for name, _, _ in INCOME_BANDS),
    "financial_literacy": tuple(level.value for level in FinancialLiteracy),
    "experience": tuple(level.value for level in InvestmentExperience),
}


@dataclass(frozen=True, slots=True)
class GroupStats:
    """One group's outcomes. Every metric is `None` when the group is suppressed."""

    group: str
    size: int
    suppressed: bool
    risk_distribution: dict[str, float] | None = None
    mean_risk_score: float | None = None
    mean_equity_weight: float | None = None
    portfolio_rate: float | None = None


@dataclass(frozen=True, slots=True)
class Disparity:
    """A four-fifths-style ratio across a dimension's reportable groups."""

    metric: str
    ratio: float
    lowest_group: str
    highest_group: str
    lowest_rate: float
    highest_rate: float
    flagged: bool


@dataclass(frozen=True, slots=True)
class DimensionReport:
    dimension: str
    label: str
    groups: tuple[GroupStats, ...]
    disparity: Disparity | None
    # Why no disparity metric, when there is none. "Not shown" and "shown as nothing
    # because everything was suppressed" are different states and a reader cannot
    # tell them apart from a null.
    note: str | None = None


@dataclass(frozen=True, slots=True)
class FairnessReport:
    population: int
    reportable_population: int
    min_group_size: int
    dimensions: tuple[DimensionReport, ...]


@dataclass(frozen=True, slots=True)
class _Observation:
    """One user's outcome row, already reduced to non-identifying values.

    Constructed inside this module and never returned. `age_band` rather than age,
    `income_band` rather than income: by the time an observation exists, the
    plaintext is gone.
    """

    age_band: str
    income_band: str
    financial_literacy: str
    experience: str
    risk_category: RiskCategory
    risk_score: float
    equity_weight: float | None


def _band_age(age: int) -> str:
    for name, low, high in AGE_BANDS:
        if low <= age <= high:
            return name
    return AGE_BANDS[-1][0]


def _band_income(income: Decimal) -> str:
    """Convert plaintext income to a band. The value does not leave this function."""
    for name, low, high in INCOME_BANDS:
        if low <= income < high:
            return name
    return INCOME_BANDS[-1][0]


def _latest_assessments(db: Session) -> dict[object, RiskAssessment]:
    """Each user's most recent risk assessment.

    A user can hold several — the table is an append-only history so a changed
    profile shows as a changed classification. Counting all of them would weight
    users who revised their profile more heavily than users who did not.
    """
    newest = (
        select(
            RiskAssessment.user_id,
            func.max(RiskAssessment.created_at).label("created_at"),
        )
        .group_by(RiskAssessment.user_id)
        .subquery()
    )
    rows = db.scalars(
        select(RiskAssessment).join(
            newest,
            (RiskAssessment.user_id == newest.c.user_id)
            & (RiskAssessment.created_at == newest.c.created_at),
        )
    )
    return {row.user_id: row for row in rows}


def _equity_weights(db: Session) -> dict[object, float]:
    """Each user's equity share in their most recent portfolio."""
    newest = (
        select(Portfolio.user_id, func.max(Portfolio.created_at).label("created_at"))
        .group_by(Portfolio.user_id)
        .subquery()
    )
    rows = db.execute(
        select(Portfolio.user_id, func.coalesce(func.sum(PortfolioAsset.weight), 0))
        .join(
            newest,
            (Portfolio.user_id == newest.c.user_id) & (Portfolio.created_at == newest.c.created_at),
        )
        .join(PortfolioAsset, PortfolioAsset.portfolio_id == Portfolio.id)
        .join(Asset, Asset.id == PortfolioAsset.asset_id)
        .where(Asset.asset_class == AssetClass.EQUITY)
        .group_by(Portfolio.user_id)
    )
    return {user_id: float(weight) for user_id, weight in rows}


def _observations(db: Session) -> list[_Observation]:
    assessments = _latest_assessments(db)
    equity = _equity_weights(db)

    observations: list[_Observation] = []
    for profile in db.scalars(select(FinancialProfile)):
        assessment = assessments.get(profile.user_id)
        if assessment is None:
            # No risk assessment means no outcome to audit. Including the user with
            # a null category would inflate every group size without adding a
            # measurement — and group size is what suppression turns on.
            continue

        observations.append(
            _Observation(
                age_band=_band_age(profile.age),
                income_band=_band_income(profile.income),
                financial_literacy=profile.financial_literacy.value,
                experience=profile.experience.value,
                risk_category=assessment.risk_category,
                risk_score=float(assessment.risk_score),
                equity_weight=equity.get(profile.user_id),
            )
        )
    return observations


def _group_stats(group: str, members: list[_Observation]) -> GroupStats:
    size = len(members)
    if size < MIN_GROUP_SIZE:
        return GroupStats(group=group, size=size, suppressed=True)

    counts: dict[str, int] = dict.fromkeys((c.value for c in RiskCategory), 0)
    for member in members:
        counts[member.risk_category.value] += 1

    with_portfolio = [m.equity_weight for m in members if m.equity_weight is not None]

    return GroupStats(
        group=group,
        size=size,
        suppressed=False,
        risk_distribution={k: round(v / size, 4) for k, v in counts.items()},
        mean_risk_score=round(sum(m.risk_score for m in members) / size, 4),
        # None rather than 0.0 when nobody in the group has generated a portfolio:
        # "no equity exposure" and "no portfolios to measure" are different facts.
        mean_equity_weight=(
            round(sum(with_portfolio) / len(with_portfolio), 4) if with_portfolio else None
        ),
        portfolio_rate=round(len(with_portfolio) / size, 4),
    )


def _disparity(groups: tuple[GroupStats, ...]) -> Disparity | None:
    """Four-fifths ratio on the HIGH-risk assignment rate.

    HIGH-risk assignment is the outcome chosen because it is the one that most
    changes what the product then does: a HIGH classification widens the per-asset
    cap and shifts the whole constraint band toward equities. Comparing mean risk
    *scores* instead would average away the threshold crossings that actually alter
    the recommendation.
    """
    reportable = [g for g in groups if not g.suppressed and g.risk_distribution is not None]
    if len(reportable) < 2:
        return None

    rates = {
        g.group: (g.risk_distribution or {}).get(RiskCategory.HIGH.value, 0.0) for g in reportable
    }
    lowest = min(rates, key=lambda k: rates[k])
    highest = max(rates, key=lambda k: rates[k])

    if rates[highest] == 0:
        # Nobody in any reportable group was classified HIGH. A ratio of 0/0 is not
        # parity and not disparity; it is an absence of the outcome being measured.
        return None

    ratio = rates[lowest] / rates[highest]
    return Disparity(
        metric="HIGH risk classification rate",
        ratio=round(ratio, 4),
        lowest_group=lowest,
        highest_group=highest,
        lowest_rate=round(rates[lowest], 4),
        highest_rate=round(rates[highest], 4),
        flagged=ratio < DISPARITY_FLAG_RATIO,
    )


def _dimension_report(dimension: str, observations: list[_Observation]) -> DimensionReport:
    buckets: dict[str, list[_Observation]] = defaultdict(list)
    for observation in observations:
        buckets[getattr(observation, dimension)].append(observation)

    ordered = DIMENSION_ORDER[dimension]
    # Every declared group appears, including empty ones. A band that vanishes from
    # the report because nobody is in it makes the population look more uniform than
    # it is, and "no users in this band" is itself an audit finding.
    groups = tuple(_group_stats(name, buckets.get(name, [])) for name in ordered)

    disparity = _disparity(groups)
    note = None
    if disparity is None:
        reportable = sum(1 for g in groups if not g.suppressed)
        note = (
            f"Fewer than two groups reach the minimum size of {MIN_GROUP_SIZE}, so no "
            "disparity ratio can be computed."
            if reportable < 2
            else "No user in a reportable group was classified HIGH risk, so there is "
            "no selection rate to compare."
        )

    return DimensionReport(
        dimension=dimension,
        label=DIMENSION_LABELS[dimension],
        groups=groups,
        disparity=disparity,
        note=note,
    )


def build_report(db: Session) -> FairnessReport:
    """FR-14's report over every user with a risk assessment."""
    observations = _observations(db)

    dimensions = tuple(_dimension_report(name, observations) for name in DIMENSION_LABELS)

    reportable = sum(
        group.size
        for dimension in dimensions
        if dimension.dimension == "age_band"
        for group in dimension.groups
        if not group.suppressed
    )

    return FairnessReport(
        population=len(observations),
        reportable_population=reportable,
        min_group_size=MIN_GROUP_SIZE,
        dimensions=dimensions,
    )
