"""FR-14 — `GET /api/v1/fairness/report` (§13.2).

Authentication only, no privilege tier: this project has no auditor role, and
inventing one to guard a single endpoint would be a larger change than the endpoint.
What makes that defensible is that the payload contains nothing individual at any
group size — `fairness_service` suppresses below §11.2's threshold before building
the response, not after. The limitation is recorded in
`Docs/PLAN/PHASE-6-HARDENING.md` §2.3 rather than left implicit.
"""

from dataclasses import asdict

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.common import ErrorResponse
from app.schemas.fairness import (
    DimensionReportOut,
    DisparityOut,
    FairnessReportOut,
    GroupStatsOut,
)
from app.services import fairness_service

router = APIRouter(prefix="/fairness", tags=["fairness"])


@router.get(
    "/report",
    response_model=FairnessReportOut,
    responses={401: {"model": ErrorResponse}},
)
def read_report(user: CurrentUser, db: DbSession) -> FairnessReportOut:
    report = fairness_service.build_report(db)

    return FairnessReportOut(
        population=report.population,
        reportable_population=report.reportable_population,
        min_group_size=report.min_group_size,
        dimensions=[
            DimensionReportOut(
                dimension=dimension.dimension,
                label=dimension.label,
                groups=[
                    GroupStatsOut(
                        group=group.group,
                        size=group.size,
                        suppressed=group.suppressed,
                        risk_distribution=group.risk_distribution,
                        mean_risk_score=group.mean_risk_score,
                        mean_equity_weight=group.mean_equity_weight,
                        portfolio_rate=group.portfolio_rate,
                    )
                    for group in dimension.groups
                ],
                # `asdict`, not `vars`: `Disparity` is a slots dataclass and so has
                # no `__dict__` for `vars()` to read. The first version used `vars()`
                # and raised TypeError — but only once a dimension actually had a
                # disparity, which needs two groups of 20+, so every test that
                # reached the endpoint went down the empty-instance branch instead.
                disparity=(
                    DisparityOut(**asdict(dimension.disparity)) if dimension.disparity else None
                ),
                note=dimension.note,
            )
            for dimension in report.dimensions
        ],
    )
