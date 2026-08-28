"""Portfolio generation (FR-10, FR-11, FR-13).

The pipeline, and where each PRD requirement is satisfied:

    risk assessment (M3)  →  asset features  →  suitability scoring   (FR-10 inputs)
                                             →  KNN candidate set     (§10.3)
                                             →  constraint set        (FR-11 caps)
                                             →  mean-variance solve   (FR-11 weights)
                                             →  reasons per holding   (FR-13)

FR-10's acceptance criterion is that the candidate list and weights come from the
optimizer rather than a static lookup table. Nothing in this path contains a preset
allocation: the only tables here are the *constraint bands*, which bound the answer
without choosing it.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger, safe_extra
from app.ml.features import technical
from app.ml.features.market import load_prices
from app.ml.portfolio import constraints as constraint_module
from app.ml.portfolio import inputs as input_module
from app.ml.portfolio import optimizer as optimizer_module
from app.ml.recommendation import candidates as candidate_module
from app.ml.recommendation import reasons as reason_module
from app.ml.recommendation import scoring as scoring_module
from app.models.asset import Asset
from app.models.enums import AssetClass
from app.models.financial_profile import FinancialProfile
from app.models.portfolio import Portfolio, PortfolioAsset
from app.models.prediction import Prediction
from app.models.recommendation import Recommendation
from app.models.risk_assessment import RiskAssessment

logger = get_logger(__name__)

VOLATILITY_WINDOW = 60
MOMENTUM_WINDOW = 60


@dataclass(frozen=True, slots=True)
class GeneratedPortfolio:
    portfolio: Portfolio
    recommendations: list[Recommendation]
    summary: str


def _latest_assessment(db: Session, user_id: uuid.UUID) -> RiskAssessment:
    assessment = db.scalar(
        select(RiskAssessment)
        .where(RiskAssessment.user_id == user_id)
        .order_by(RiskAssessment.created_at.desc())
        .limit(1)
    )
    if assessment is None:
        raise AppError(
            422,
            "risk_assessment_required",
            "A risk assessment is required before a portfolio can be generated. "
            "Call POST /api/v1/risk/analyze first.",
        )
    return assessment


def _asset_features(db: Session) -> list[scoring_module.AssetFeatures]:
    """Per-asset metrics for scoring, from stored prices and predictions.

    Assets without enough history are skipped rather than defaulted — a fabricated
    volatility would be treated by the optimizer as a measurement.
    """
    predictions: dict[uuid.UUID, tuple[float, float, int]] = {}
    for asset_id, predicted, confidence, horizon in db.execute(
        select(
            Prediction.asset_id,
            Prediction.predicted_return,
            Prediction.confidence,
            Prediction.horizon_days,
        ).order_by(Prediction.prediction_date.desc())
    ).all():
        predictions.setdefault(asset_id, (float(predicted), float(confidence), int(horizon)))

    features: list[scoring_module.AssetFeatures] = []
    for asset in db.scalars(select(Asset).where(Asset.is_active.is_(True)).order_by(Asset.symbol)):
        prices = load_prices(db, asset.symbol)
        if len(prices) < input_module.MIN_HISTORY_DAYS:
            continue

        close = prices["adj_close"]
        volatility = technical.volatility(close, VOLATILITY_WINDOW).iloc[-1]
        momentum = technical.momentum(close, MOMENTUM_WINDOW).iloc[-1]
        historical = float(technical.log_returns(close).mean() * input_module.TRADING_DAYS)

        if not (np.isfinite(volatility) and np.isfinite(momentum)):
            continue

        predicted, confidence, horizon = predictions.get(asset.id, (0.0, 0.0, 20))
        annualised = predicted * (input_module.TRADING_DAYS / max(horizon, 1))
        weight = input_module.ML_WEIGHT_AT_FULL_CONFIDENCE * max(0.0, min(1.0, confidence))
        expected = float(
            np.clip(
                weight * annualised + (1 - weight) * historical,
                input_module.MU_FLOOR,
                input_module.MU_CEILING,
            )
        )

        features.append(
            scoring_module.AssetFeatures(
                symbol=asset.symbol,
                asset_class=asset.asset_class,
                expected_return=expected,
                volatility=float(volatility),
                momentum=float(momentum),
                confidence=confidence,
            )
        )
    return features


def generate(db: Session, user_id: uuid.UUID, profile: FinancialProfile) -> GeneratedPortfolio:
    """Run the full FR-10 pipeline and persist the result."""
    assessment = _latest_assessment(db, user_id)

    features = _asset_features(db)
    if len(features) < 2:
        raise AppError(
            422,
            "insufficient_market_data",
            "Not enough assets have sufficient price history to build a portfolio. "
            "Run the market ingestion job and try again.",
        )

    scored = scoring_module.score_assets(
        features, risk_category=assessment.risk_category, goal=profile.investment_goal
    )
    # The bands are resolved before selection so the candidate set is guaranteed to
    # be able to satisfy the floors that will later be applied to it.
    bands, _ = constraint_module.build_bands(
        risk_category=assessment.risk_category,
        goal=profile.investment_goal,
        horizon_years=profile.investment_horizon,
    )
    # How many assets each class actually has, so the requirement below is computed
    # against the real universe rather than an imagined one.
    supply: dict[AssetClass, int] = {}
    for asset in features:
        supply[asset.asset_class] = supply.get(asset.asset_class, 0) + 1

    candidate_set = candidate_module.select_candidates(
        scored,
        risk_category=assessment.risk_category,
        required_per_class=constraint_module.required_asset_counts(
            bands,
            constraint_module.MAX_WEIGHT_PER_ASSET[assessment.risk_category],
            available=supply,
            baseline=candidate_module.MIN_PER_CLASS,
        ),
    )

    by_symbol = {asset.symbol: asset for asset in candidate_set.assets}
    symbols = list(by_symbol)

    try:
        optimizer_inputs = input_module.build_inputs(db, symbols)
    except input_module.InsufficientDataError as exc:
        raise AppError(422, "insufficient_market_data", str(exc)) from exc

    # `build_inputs` drops anything with non-overlapping history, so the class list
    # is rebuilt from what survived rather than from what was requested.
    classes = [by_symbol[symbol].features.asset_class for symbol in optimizer_inputs.symbols]

    try:
        constraint_set = constraint_module.build_constraints(
            risk_category=assessment.risk_category,
            goal=profile.investment_goal,
            horizon_years=profile.investment_horizon,
            asset_classes=classes,
        )
    except constraint_module.InfeasibleConstraintsError as exc:
        raise AppError(422, "infeasible_constraints", exc.message, exc.detail) from exc

    try:
        solution = optimizer_module.optimize(
            optimizer_inputs.mu,
            optimizer_inputs.sigma,
            constraint_set,
            optimizer_inputs.symbols,
        )
    except optimizer_module.OptimizationFailedError as exc:
        raise AppError(422, "optimization_failed", exc.message, exc.detail) from exc

    return _persist(
        db,
        user_id=user_id,
        assessment=assessment,
        profile=profile,
        solution=solution,
        constraint_set=constraint_set,
        scored=by_symbol,
    )


def _persist(
    db: Session,
    *,
    user_id: uuid.UUID,
    assessment: RiskAssessment,
    profile: FinancialProfile,
    solution: optimizer_module.OptimizedPortfolio,
    constraint_set: constraint_module.ConstraintSet,
    scored: dict[str, scoring_module.ScoredAsset],
) -> GeneratedPortfolio:
    holdings = solution.holdings()

    summary = reason_module.portfolio_summary(
        risk_category=assessment.risk_category,
        goal=profile.investment_goal,
        horizon_years=profile.investment_horizon,
        expected_return=solution.expected_return,
        expected_risk=solution.expected_risk,
        constraint_notes=constraint_set.notes,
        holdings=len(holdings),
    )

    objective = constraint_set.as_dict()
    objective["summary"] = summary
    objective["mu_source"] = (
        "ML predictions shrunk toward the historical mean, weighted by model "
        "confidence (Phase 4 plan, decision 2)."
    )

    portfolio = Portfolio(
        user_id=user_id,
        expected_return=Decimal(str(round(solution.expected_return, 6))),
        expected_risk=Decimal(str(round(solution.expected_risk, 6))),
        risk_category=assessment.risk_category,
        model_version=assessment.model_version,
        objective=objective,
    )
    db.add(portfolio)
    db.flush()

    asset_ids = {
        symbol: asset_id
        for symbol, asset_id in db.execute(
            select(Asset.symbol, Asset.id).where(Asset.symbol.in_(list(holdings)))
        ).all()
    }

    recommendations: list[Recommendation] = []
    for symbol, weight in sorted(holdings.items(), key=lambda item: -item[1]):
        db.add(
            PortfolioAsset(
                portfolio_id=portfolio.id,
                asset_id=asset_ids[symbol],
                weight=Decimal(str(round(weight, 8))),
            )
        )

        asset = scored[symbol]
        recommendation = Recommendation(
            user_id=user_id,
            asset_id=asset_ids[symbol],
            portfolio_id=portfolio.id,
            score=Decimal(str(round(asset.score, 6))),
            # FR-13 — never null, and derived from this asset's own score components
            # rather than composed to sound convincing.
            reason=reason_module.asset_reason(
                asset,
                weight=weight,
                risk_category=assessment.risk_category,
                goal=profile.investment_goal,
            ),
            model_version=assessment.model_version,
        )
        db.add(recommendation)
        recommendations.append(recommendation)

    db.commit()
    db.refresh(portfolio)

    # Category and counts only — expected return is derived from the profile, and
    # the profile is financial PII (§11.2).
    logger.info(
        "portfolio_generated",
        extra=safe_extra(
            user_id=str(user_id),
            risk_category=str(assessment.risk_category),
            holdings=len(holdings),
            model_version=assessment.model_version,
        ),
    )
    return GeneratedPortfolio(portfolio=portfolio, recommendations=recommendations, summary=summary)


def current(db: Session, user_id: uuid.UUID) -> Portfolio | None:
    return db.scalar(
        select(Portfolio)
        .where(Portfolio.user_id == user_id)
        .order_by(Portfolio.created_at.desc())
        .limit(1)
    )


def get_recommendation(
    db: Session, user_id: uuid.UUID, recommendation_id: uuid.UUID
) -> Recommendation:
    """Scoped by user_id, so another user's recommendation is a 404 rather than a
    403 — the existence of someone else's recommendation is not ours to confirm."""
    recommendation = db.scalar(
        select(Recommendation).where(
            Recommendation.id == recommendation_id, Recommendation.user_id == user_id
        )
    )
    if recommendation is None:
        raise AppError(404, "recommendation_not_found", "No such recommendation for this account.")
    return recommendation
