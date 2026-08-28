"""Human-readable reasons for each recommendation (FR-13).

FR-13's acceptance criterion: "at least one plain-language reason is attached before
it is shown to the user (baseline: rule-derived reason string)". SHAP/LIME is listed
as the advanced option and belongs to M6.

The rule this module follows: **a reason must be derived from the numbers that
actually drove the decision, never composed to sound convincing.** Every sentence
below is generated from a component of the score or from the constraint set that was
in force, and the figures quoted are the figures used. A recommendation surface that
generates persuasive-sounding text detached from the computation is worse than no
explanation at all — it manufactures confidence the system has not earned, on a
subject where PRD §17 exists precisely because misplaced confidence has consequences.

That is also why the phrasing stays descriptive. "This asset's volatility is close to
the target for your risk profile" is a statement about the calculation. "This is a
great fit for you" is a claim about outcomes, and this is not a system that gets to
make those (PRD §5, §17.2).
"""

from app.ml.recommendation.scoring import ScoredAsset
from app.models.enums import AssetClass, InvestmentGoal, RiskCategory

# Which component drove the score → how to say so.
COMPONENT_PHRASES: dict[str, str] = {
    "expected_return": "its expected return is among the highest in the candidate set",
    "volatility_fit": "its volatility is close to the level targeted for your risk profile",
    "momentum": "its recent trend is among the stronger ones in the candidate set",
    "prediction_confidence": "the market model's prediction for it is comparatively stable",
    "goal_fit": "its asset class suits your stated investment goal",
}

CLASS_DESCRIPTIONS: dict[AssetClass, str] = {
    AssetClass.EQUITY: "equity exposure",
    AssetClass.BOND: "fixed income, which dampens portfolio volatility",
    AssetClass.COMMODITY: "commodity exposure, which diversifies against equities",
    AssetClass.REAL_ESTATE: "real-estate exposure",
    AssetClass.CASH: "cash-like stability",
}


def asset_reason(
    asset: ScoredAsset,
    *,
    weight: float,
    risk_category: RiskCategory,
    goal: InvestmentGoal,
) -> str:
    """One plain-language sentence per holding, built from its own score components."""
    ranked = sorted(asset.components.items(), key=lambda item: item[1], reverse=True)
    drivers = [COMPONENT_PHRASES[name] for name, value in ranked[:2] if value > 0]

    role = CLASS_DESCRIPTIONS.get(asset.features.asset_class, "diversification")

    parts = [
        f"{asset.symbol} is allocated {weight:.1%} because {drivers[0]}"
        if drivers
        else f"{asset.symbol} is allocated {weight:.1%} for {role}"
    ]
    if len(drivers) > 1:
        parts.append(f", and {drivers[1]}")
    parts.append(f". It contributes {role} to a {risk_category} portfolio")
    parts.append(f" aimed at {str(goal).replace('_', ' ').lower()}.")

    # The measured figures behind the sentence, so the claim is checkable rather
    # than merely readable.
    parts.append(
        f" Measured annualised volatility {asset.features.volatility:.1%};"
        f" expected return {asset.features.expected_return:.1%}."
    )
    return "".join(parts)


def portfolio_summary(
    *,
    risk_category: RiskCategory,
    goal: InvestmentGoal,
    horizon_years: int,
    expected_return: float,
    expected_risk: float,
    constraint_notes: list[str],
    holdings: int,
) -> str:
    """The portfolio-level explanation, including the constraints that shaped it.

    The constraint notes matter more than the numbers here: "why is this only 12%
    equities?" is answered by the band that was in force, not by the objective value.
    """
    lines = [
        f"This {holdings}-holding portfolio was optimised for a {risk_category} risk "
        f"profile with a {horizon_years}-year horizon, aimed at "
        f"{str(goal).replace('_', ' ').lower()}.",
        f"Its expected annual return is {expected_return:.1%} with an expected "
        f"annualised volatility of {expected_risk:.1%}.",
        "Weights were produced by a mean-variance optimiser under the following "
        "constraints, not selected from a preset allocation:",
    ]
    lines.extend(f"— {note}" for note in constraint_notes)
    lines.append(
        "Expected return and volatility are model estimates from historical data and "
        "are not forecasts of what this portfolio will do."
    )
    return "\n".join(lines)
