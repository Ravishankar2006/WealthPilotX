"""§19 backtesting for a stored portfolio.

Extracted from `app/jobs/ml.py` when the API gained a backtest endpoint. Two callers
now need this logic — the CLI and `GET /portfolio/backtest` — and a backtest that
gives different answers depending on which one asked would be worse than no backtest
at all.

Two rules carried over from the job, because they are the substance rather than the
plumbing:

**The window follows the training period.** §19 requires a backtest period separate
from the training period. The window start is pushed past the production model's
`training_end` rather than the request being refused, because a caller has no way to
know where that boundary falls — and the window actually used is reported back, so
nobody has to assume they got the one they asked for.

**It measures a stored portfolio, not a fresh one.** The thing worth measuring is
what a user was actually shown, not what the optimiser would produce today against
prices it can now see.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ml import backtest, registry
from app.ml.features.market import load_prices
from app.models.asset import Asset
from app.models.model_record import PREDICTION_MODEL
from app.models.portfolio import Portfolio, PortfolioAsset

DEFAULT_MONTHS = 12

# Below this there is not enough out-of-sample data for the §19 metrics to mean
# anything: annualising a six-week return produces a number with the shape of a
# yearly figure and none of its content.
MIN_WINDOW_DAYS = 60


class BacktestUnavailableError(Exception):
    """The backtest cannot be run, with a reason a caller can act on.

    Every case is operational rather than exceptional — no benchmark history, a
    training window that consumed the available data — so callers translate this to
    a 503 or a printed message, never a 500.
    """


@dataclass(frozen=True, slots=True)
class PortfolioBacktest:
    result: backtest.BacktestResult
    portfolio_id: object
    months_requested: int
    training_end: date | None


def _weights(db: Session, portfolio: Portfolio) -> dict[str, float]:
    return {
        symbol: float(weight)
        for weight, symbol in db.execute(
            select(PortfolioAsset.weight, Asset.symbol)
            .join(Asset, Asset.id == PortfolioAsset.asset_id)
            .where(PortfolioAsset.portfolio_id == portfolio.id)
        ).all()
    }


def run_for_portfolio(
    db: Session,
    portfolio: Portfolio,
    *,
    months: int = DEFAULT_MONTHS,
    cost_bps: float = backtest.DEFAULT_TRANSACTION_COST_BPS,
) -> PortfolioBacktest:
    """Backtest one stored portfolio against the benchmark."""
    weights = _weights(db, portfolio)
    if not weights:
        raise BacktestUnavailableError("This portfolio has no holdings to backtest.")

    frames: dict[str, pd.Series] = {}
    for symbol in [*weights, backtest.DEFAULT_BENCHMARK]:
        prices = load_prices(db, symbol)
        if not prices.empty:
            frames[symbol] = prices["adj_close"]

    if backtest.DEFAULT_BENCHMARK not in frames:
        raise BacktestUnavailableError(
            f"The benchmark {backtest.DEFAULT_BENCHMARK} has no stored price history, so "
            "there is nothing to compare against. §19 requires a benchmark comparison."
        )

    prices = pd.DataFrame(frames).sort_index()
    end = prices.index[-1].date()

    record = registry.production_record(db, PREDICTION_MODEL)
    training_end = record.training_end if record else None

    start = end - timedelta(days=int(months * 30.44))
    if training_end is not None and start <= training_end:
        start = training_end + timedelta(days=1)

    if (end - start).days < MIN_WINDOW_DAYS:
        raise BacktestUnavailableError(
            f"Only {(end - start).days} days of price history fall outside the model's "
            f"training window (through {training_end}). Retrain with a reserved period — "
            "`python -m app.jobs train-prediction --holdout-days 180` — so there is "
            "genuine out-of-sample data to measure against (§19)."
        )

    try:
        result = backtest.run(
            prices,
            weights,
            benchmark=prices[backtest.DEFAULT_BENCHMARK],
            start=start,
            end=end,
            training_end=training_end,
            transaction_cost_bps=cost_bps,
        )
    except backtest.BacktestError as exc:
        raise BacktestUnavailableError(str(exc)) from exc

    return PortfolioBacktest(
        result=result,
        portfolio_id=portfolio.id,
        months_requested=months,
        training_end=training_end,
    )


def latest_portfolio(db: Session, user_id: object) -> Portfolio | None:
    return db.scalar(
        select(Portfolio)
        .where(Portfolio.user_id == user_id)
        .order_by(Portfolio.created_at.desc())
        .limit(1)
    )


def sample_equity_curve(curve: pd.Series, limit: int = 260) -> list[dict[str, Any]]:
    """Thin the curve to at most `limit` points for transport.

    A multi-year daily curve is a few hundred points, which is fine — but the window
    is caller-controlled, and a chart cannot render more resolution than it has
    pixels anyway. Sampling by stride rather than by resampling to a calendar period
    keeps the first and last points exact, which is what the total-return figure is
    computed from: a curve whose endpoints disagreed with the headline number would
    be worse than no curve.
    """
    if curve.empty:
        return []

    stride = max(1, len(curve) // limit)
    sampled = curve.iloc[::stride]
    if sampled.index[-1] != curve.index[-1]:
        sampled = pd.concat([sampled, curve.iloc[[-1]]])

    return [
        {"date": index.date().isoformat(), "value": round(float(value), 6)}
        for index, value in sampled.items()
    ]
