"""FR-14 — the fairness report, and the suppression that makes it publishable.

The tests that matter here are the negative ones. A fairness dashboard is easy to
build so that it always shows something, and "always shows something" is exactly the
failure mode §11.2's minimum group size exists to prevent: on a small population,
a group statistic is a description of identifiable people.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import (
    FinancialLiteracy,
    InvestmentExperience,
    InvestmentGoal,
    RiskAppetite,
    RiskCategory,
)
from app.models.financial_profile import FinancialProfile
from app.models.risk_assessment import RiskAssessment
from app.models.user import User
from app.schemas.fairness import FAIRNESS_DISCLAIMER
from app.services import fairness_service

# A digit string that appears in no weight, score, count or ratio the report can
# produce. The M4 suite learned this the hard way: an income of 25000 "leaked" into
# a test only because `0.25000000` is a legitimate portfolio weight.
DISTINCTIVE_INCOME = Decimal("81234.56")
DISTINCTIVE_SAVINGS = Decimal("47531.09")


def _make_user(
    db: Session,
    *,
    age: int,
    category: RiskCategory,
    score: str = "0.5",
    literacy: FinancialLiteracy = FinancialLiteracy.MEDIUM,
    experience: InvestmentExperience = InvestmentExperience.BEGINNER,
    income: Decimal = DISTINCTIVE_INCOME,
) -> User:
    user = User(
        email=f"fair-{uuid.uuid4().hex[:12]}@example.com",
        password_hash="not-a-real-hash",
        tos_accepted_at=datetime.now(UTC),
    )
    db.add(user)
    db.flush()

    db.add(
        FinancialProfile(
            user_id=user.id,
            age=age,
            income=income,
            savings=DISTINCTIVE_SAVINGS,
            risk_appetite=RiskAppetite.MODERATE,
            investment_goal=InvestmentGoal.GROWTH,
            investment_horizon=10,
            experience=experience,
            financial_literacy=literacy,
        )
    )
    db.add(
        RiskAssessment(
            user_id=user.id,
            model_version="v1",
            risk_score=Decimal(score),
            risk_category=category,
            top_factors=[],
        )
    )
    return user


def _populate(db: Session, count: int, **kwargs: object) -> None:
    for _ in range(count):
        _make_user(db, **kwargs)  # type: ignore[arg-type]
    db.commit()


def _dimension(report: object, name: str) -> object:
    dimensions = report["dimensions"] if isinstance(report, dict) else report.dimensions  # type: ignore[index]
    for dimension in dimensions:
        key = dimension["dimension"] if isinstance(dimension, dict) else dimension.dimension
        if key == name:
            return dimension
    raise AssertionError(f"no dimension {name!r} in the report")


class TestSuppression:
    def test_a_group_one_short_of_the_threshold_is_suppressed(self, db: Session) -> None:
        _populate(db, fairness_service.MIN_GROUP_SIZE - 1, age=35, category=RiskCategory.HIGH)

        report = fairness_service.build_report(db)
        group = next(
            g
            for g in _dimension(report, "age_band").groups
            if g.group == "30-44"  # type: ignore[attr-defined]
        )

        assert group.size == fairness_service.MIN_GROUP_SIZE - 1
        assert group.suppressed is True

    def test_a_group_at_the_threshold_is_reported(self, db: Session) -> None:
        _populate(db, fairness_service.MIN_GROUP_SIZE, age=35, category=RiskCategory.HIGH)

        group = next(
            g
            for g in _dimension(fairness_service.build_report(db), "age_band").groups  # type: ignore[attr-defined]
            if g.group == "30-44"
        )

        assert group.suppressed is False
        assert group.risk_distribution == {"LOW": 0.0, "MEDIUM": 0.0, "HIGH": 1.0}

    def test_a_suppressed_group_reports_null_not_zero(self, db: Session) -> None:
        """The distinction the whole threshold rests on. A rate of `0.0` on a group
        of three reads as a measurement of those three people."""
        _populate(db, 3, age=25, category=RiskCategory.LOW)

        group = next(
            g
            for g in _dimension(fairness_service.build_report(db), "age_band").groups  # type: ignore[attr-defined]
            if g.group == "18-29"
        )

        assert group.suppressed is True
        assert group.risk_distribution is None
        assert group.mean_risk_score is None
        assert group.mean_equity_weight is None
        assert group.portfolio_rate is None

    def test_empty_bands_are_still_listed(self, db: Session) -> None:
        """A band that disappears because nobody is in it makes the population look
        more uniform than it is."""
        _populate(db, 25, age=35, category=RiskCategory.MEDIUM)

        bands = {g.group for g in _dimension(fairness_service.build_report(db), "age_band").groups}  # type: ignore[attr-defined]
        assert bands == {name for name, _, _ in fairness_service.AGE_BANDS}


class TestDisparity:
    def test_it_flags_a_selection_rate_below_four_fifths(self, db: Session) -> None:
        # 20 young users, all HIGH. 20 older users, 2 HIGH — a rate of 0.1 against
        # 1.0, well under the four-fifths screen.
        _populate(db, 20, age=25, category=RiskCategory.HIGH)
        _populate(db, 2, age=65, category=RiskCategory.HIGH)
        _populate(db, 18, age=65, category=RiskCategory.LOW)

        disparity = _dimension(fairness_service.build_report(db), "age_band").disparity  # type: ignore[attr-defined]

        assert disparity is not None
        assert disparity.highest_group == "18-29"
        assert disparity.lowest_group == "60+"
        assert disparity.ratio == pytest.approx(0.1)
        assert disparity.flagged is True

    def test_parity_is_not_flagged(self, db: Session) -> None:
        _populate(db, 20, age=25, category=RiskCategory.MEDIUM)
        _populate(db, 20, age=65, category=RiskCategory.MEDIUM)
        _populate(db, 20, age=25, category=RiskCategory.HIGH)
        _populate(db, 20, age=65, category=RiskCategory.HIGH)

        disparity = _dimension(fairness_service.build_report(db), "age_band").disparity  # type: ignore[attr-defined]

        assert disparity is not None
        assert disparity.ratio == pytest.approx(1.0)
        assert disparity.flagged is False

    def test_one_reportable_group_yields_no_ratio_and_says_why(self, db: Session) -> None:
        _populate(db, 25, age=35, category=RiskCategory.HIGH)

        dimension = _dimension(fairness_service.build_report(db), "age_band")

        assert dimension.disparity is None  # type: ignore[attr-defined]
        assert "Fewer than two groups" in (dimension.note or "")  # type: ignore[attr-defined]

    def test_no_high_classifications_is_reported_as_absence_not_parity(self, db: Session) -> None:
        """0/0 is neither parity nor disparity, and returning 1.0 would claim it
        was the former."""
        _populate(db, 20, age=25, category=RiskCategory.LOW)
        _populate(db, 20, age=65, category=RiskCategory.LOW)

        dimension = _dimension(fairness_service.build_report(db), "age_band")

        assert dimension.disparity is None  # type: ignore[attr-defined]
        assert "no selection rate to compare" in (dimension.note or "")  # type: ignore[attr-defined]


class TestEndpoint:
    def test_it_requires_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/fairness/report").status_code == 401

    def test_an_empty_instance_reports_zero_rather_than_failing(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """The state every fresh deployment is in. A fairness page that crashes or
        renders an empty chart on day one is worse than one that says so."""
        body = client.get("/api/v1/fairness/report", headers=auth_headers).json()

        assert body["population"] == 0
        assert body["min_group_size"] == fairness_service.MIN_GROUP_SIZE
        assert len(body["dimensions"]) == 4
        for dimension in body["dimensions"]:
            assert all(group["suppressed"] for group in dimension["groups"])
            assert dimension["disparity"] is None
            assert dimension["note"]

    def test_no_raw_financial_value_reaches_the_response(
        self, client: TestClient, auth_headers: dict[str, str], db: Session
    ) -> None:
        """§11.2 — income and savings are financial PII. The report bands them; the
        plaintext must not survive anywhere in the payload, at any group size."""
        _populate(db, 25, age=35, category=RiskCategory.HIGH)

        raw = client.get("/api/v1/fairness/report", headers=auth_headers).text

        assert "81234" not in raw
        assert "47531" not in raw
        # The band label is what a reader gets instead.
        assert "50k-100k" in raw

    def test_it_carries_the_section_17_1_disclaimer(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = client.get("/api/v1/fairness/report", headers=auth_headers).json()
        assert body["disclaimer"] == FAIRNESS_DISCLAIMER

    def test_users_without_a_risk_assessment_are_not_counted(
        self, client: TestClient, auth_headers: dict[str, str], db: Session
    ) -> None:
        """Including them would inflate group sizes past the suppression threshold
        without adding a single measured outcome."""
        user = User(
            email="no-assessment@example.com",
            password_hash="x",
            tos_accepted_at=datetime.now(UTC),
        )
        db.add(user)
        db.flush()
        db.add(
            FinancialProfile(
                user_id=user.id,
                age=40,
                income=DISTINCTIVE_INCOME,
                savings=DISTINCTIVE_SAVINGS,
                risk_appetite=RiskAppetite.MODERATE,
                investment_goal=InvestmentGoal.GROWTH,
                investment_horizon=10,
                experience=InvestmentExperience.BEGINNER,
                financial_literacy=FinancialLiteracy.MEDIUM,
            )
        )
        db.commit()

        body = client.get("/api/v1/fairness/report", headers=auth_headers).json()
        assert body["population"] == 0


class TestBanding:
    @pytest.mark.parametrize(
        ("age", "expected"),
        [(18, "18-29"), (29, "18-29"), (30, "30-44"), (59, "45-59"), (60, "60+"), (95, "60+")],
    )
    def test_age_bands_have_no_gaps(self, age: int, expected: str) -> None:
        assert fairness_service._band_age(age) == expected

    @pytest.mark.parametrize(
        ("income", "expected"),
        [
            ("0", "under 50k"),
            ("49999.99", "under 50k"),
            ("50000", "50k-100k"),
            ("199999", "100k-200k"),
            ("200000", "200k+"),
            ("9000000", "200k+"),
        ],
    )
    def test_income_bands_have_no_gaps(self, income: str, expected: str) -> None:
        assert fairness_service._band_income(Decimal(income)) == expected
