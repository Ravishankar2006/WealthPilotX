"""Expected returns and covariance for the optimizer (Phase 4 plan, decision 2).

Mean-variance optimisation is far more sensitive to its inputs than to its
objective, and both inputs here are deliberately conservative estimates rather than
best guesses. Two independent brakes:

* **μ is shrunk toward the historical mean**, by an amount scaled to the prediction
  model's own confidence. M3's predictor has an R² near zero; feeding it in raw would
  let the optimizer chase noise into an 80%-one-ticker portfolio. An unconfident
  prediction decays to the historical mean, so the system degrades to a dull
  portfolio rather than a reckless one.
* **Σ is Ledoit-Wolf shrunk**, not the sample covariance. With 32 assets and a few
  hundred observations the sample matrix is near-singular, and mean-variance inverts
  it — which is exactly where its reputation for nonsense weights comes from.
"""

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ml.features import technical
from app.ml.features.market import load_prices
from app.models.asset import Asset
from app.models.prediction import Prediction

TRADING_DAYS = 252

# How much of the ML view to take at full confidence. 0.35 rather than something
# bolder because the model has to earn more than that, and it has not: on the
# measured metrics it does not beat predicting the mean.
ML_WEIGHT_AT_FULL_CONFIDENCE = 0.35

# Annualised bounds on any single expected return. A μ outside this band is an
# artifact of a short or unusual sample, and the optimizer would treat it as a fact.
MU_FLOOR = -0.30
MU_CEILING = 0.40

# Minimum history before an asset can appear in an optimization at all.
MIN_HISTORY_DAYS = 120


@dataclass(frozen=True, slots=True)
class OptimizerInputs:
    """Everything the optimizer needs, aligned on one ordered symbol list."""

    symbols: tuple[str, ...]
    mu: np.ndarray
    sigma: np.ndarray
    asset_ids: tuple[str, ...]
    # Kept for the explanation layer: "we expected 6% because the model said 9% and
    # history said 4%" is answerable only if both halves survive.
    mu_ml: dict[str, float]
    mu_historical: dict[str, float]
    confidence: dict[str, float]
    as_of: date

    @property
    def size(self) -> int:
        return len(self.symbols)


def historical_returns(db: Session, symbols: list[str]) -> pd.DataFrame:
    """Daily log returns per symbol, aligned on shared trading days.

    Inner-aligned on purpose: a covariance estimated from unequal windows is not a
    covariance. Assets whose history does not overlap the rest are dropped upstream.
    """
    series: dict[str, pd.Series] = {}
    for symbol in symbols:
        prices = load_prices(db, symbol)
        if len(prices) < MIN_HISTORY_DAYS:
            continue
        series[symbol] = technical.log_returns(prices["adj_close"]).dropna()

    if not series:
        return pd.DataFrame()

    return pd.DataFrame(series).dropna()


def annualised_mean(returns: pd.DataFrame) -> pd.Series:
    return returns.mean() * TRADING_DAYS


def shrunk_covariance(returns: pd.DataFrame) -> np.ndarray:
    """Annualised Ledoit-Wolf covariance.

    Ledoit-Wolf pulls the sample matrix toward a scaled identity by an analytically
    chosen amount. The result is well-conditioned and invertible, which the sample
    matrix frequently is not at this asset-count-to-observation ratio.
    """
    estimator = LedoitWolf().fit(returns.to_numpy())
    return np.asarray(estimator.covariance_) * TRADING_DAYS


def latest_predictions(db: Session, symbols: list[str]) -> dict[str, tuple[float, float]]:
    """The newest prediction per symbol, as (annualised return, confidence).

    M3 predicts a 20-day log return; the optimizer works in annual terms, so it is
    scaled by 252/20. That scaling assumes the predicted edge persists for a year,
    which it will not — one more reason the shrinkage in `blended_mu` is not
    optional.
    """
    rows = db.execute(
        select(
            Asset.symbol,
            Prediction.predicted_return,
            Prediction.confidence,
            Prediction.horizon_days,
            Prediction.prediction_date,
        )
        .join(Asset, Asset.id == Prediction.asset_id)
        .where(Asset.symbol.in_(symbols))
        .order_by(Asset.symbol, Prediction.prediction_date.desc())
    ).all()

    latest: dict[str, tuple[float, float]] = {}
    for symbol, predicted, confidence, horizon, _ in rows:
        if symbol in latest:
            continue  # ordered newest first, so the first row per symbol wins
        scale = TRADING_DAYS / max(int(horizon), 1)
        latest[symbol] = (float(predicted) * scale, float(confidence))
    return latest


def blended_mu(
    historical: pd.Series, predictions: dict[str, tuple[float, float]]
) -> tuple[pd.Series, dict[str, float], dict[str, float]]:
    """Blend the ML view with the historical mean, weighted by confidence.

    Returns the blended μ plus the two components, so an explanation can show its
    working rather than asserting a number.
    """
    blended: dict[str, float] = {}
    ml_component: dict[str, float] = {}
    confidences: dict[str, float] = {}

    for symbol, hist in historical.items():
        name = str(symbol)
        predicted, confidence = predictions.get(name, (float(hist), 0.0))
        weight = ML_WEIGHT_AT_FULL_CONFIDENCE * max(0.0, min(1.0, confidence))

        value = weight * predicted + (1.0 - weight) * float(hist)
        blended[name] = float(np.clip(value, MU_FLOOR, MU_CEILING))
        ml_component[name] = predicted
        confidences[name] = confidence

    return pd.Series(blended), ml_component, confidences


def build_inputs(db: Session, symbols: list[str]) -> OptimizerInputs:
    """Assemble μ and Σ for a symbol set, dropping anything with too little history."""
    returns = historical_returns(db, symbols)
    if returns.empty or returns.shape[1] < 2:
        raise InsufficientDataError(
            "At least two assets with overlapping price history are needed to "
            "optimize a portfolio. Run `python -m app.jobs ingest-market "
            "--backfill-days 1100` and check the tracked universe."
        )

    ordered = [str(column) for column in returns.columns]
    historical = annualised_mean(returns)
    predictions = latest_predictions(db, ordered)
    mu, mu_ml, confidence = blended_mu(historical, predictions)

    asset_ids = {
        symbol: str(asset_id)
        for symbol, asset_id in db.execute(
            select(Asset.symbol, Asset.id).where(Asset.symbol.in_(ordered))
        ).all()
    }

    return OptimizerInputs(
        symbols=tuple(ordered),
        mu=mu.reindex(ordered).to_numpy(dtype=float),
        sigma=shrunk_covariance(returns),
        asset_ids=tuple(asset_ids[symbol] for symbol in ordered),
        mu_ml=mu_ml,
        mu_historical={str(k): float(v) for k, v in historical.items()},
        confidence=confidence,
        as_of=returns.index[-1].date(),
    )


class InsufficientDataError(Exception):
    """Not enough price history to optimize. A 422 at the API boundary — the request
    is fine, the data behind it is not yet there."""
