"""Historical backtesting (PRD §19).

§19's requirements, and how each is met:

* **Train and backtest periods must not overlap.** `split_date` divides them, and
  `run` refuses if the requested backtest window starts before the models' training
  data ended. A backtest over data the model was fitted on measures memorisation.
* **Compare against a market benchmark.** SPY, reported alongside every run rather
  than optionally — a portfolio return with no benchmark is uninterpretable, and
  omitting the comparison when it is unflattering is the failure mode the
  requirement exists to prevent.
* **Total return, annualised return, volatility, Sharpe, maximum drawdown.** All five,
  for the portfolio and the benchmark alike.
* **Report the transaction-cost assumption.** Costs are applied on turnover at each
  rebalance and reported in the result. §19 added this specifically so results are
  not misleadingly frictionless.

Rebalancing is monthly, matching M3's 20-day prediction horizon. Rebalancing daily
on a monthly signal is churn: it would multiply costs without changing the view.
"""

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

TRADING_DAYS = 252

# One side of a round trip, in basis points. 10 bps is a defensible retail estimate
# for liquid US ETFs — the figure matters less than its being stated and applied.
DEFAULT_TRANSACTION_COST_BPS = 10.0

DEFAULT_BENCHMARK = "SPY"
REBALANCE_DAYS = 21

# The risk-free rate used in the Sharpe ratio. Held as a named constant rather than
# assumed zero, because a zero rate silently flatters every Sharpe figure.
RISK_FREE_RATE = 0.02


@dataclass(frozen=True, slots=True)
class Metrics:
    total_return: float
    annualised_return: float
    volatility: float
    sharpe_ratio: float
    max_drawdown: float

    def as_dict(self) -> dict[str, float]:
        return {
            "total_return": round(self.total_return, 6),
            "annualised_return": round(self.annualised_return, 6),
            "volatility": round(self.volatility, 6),
            "sharpe_ratio": round(self.sharpe_ratio, 6),
            "max_drawdown": round(self.max_drawdown, 6),
        }


@dataclass(frozen=True, slots=True)
class BacktestResult:
    start: date
    end: date
    rebalances: int
    portfolio: Metrics
    benchmark: Metrics
    benchmark_symbol: str
    transaction_cost_bps: float
    total_costs: float
    equity_curve: pd.Series = field(repr=False, default_factory=pd.Series)


class BacktestError(Exception):
    """The backtest cannot be run as specified — overlapping periods, or no data."""


def compute_metrics(returns: pd.Series) -> Metrics:
    """§19's five metrics from a daily return series."""
    clean = returns.dropna()
    if clean.empty:
        return Metrics(0.0, 0.0, 0.0, 0.0, 0.0)

    curve = (1.0 + clean).cumprod()
    total = float(curve.iloc[-1] - 1.0)

    years = len(clean) / TRADING_DAYS
    # Guard the fractional power: a negative cumulative value would produce a
    # complex number, and a sub-day window would produce an absurd annualisation.
    annualised = float((1.0 + total) ** (1.0 / years) - 1.0) if years > 0 and total > -1.0 else 0.0

    volatility = float(clean.std(ddof=0) * np.sqrt(TRADING_DAYS))
    sharpe = float((annualised - RISK_FREE_RATE) / volatility) if volatility > 1e-12 else 0.0

    running_peak = curve.cummax()
    drawdown = curve / running_peak - 1.0
    max_drawdown = float(drawdown.min())

    return Metrics(total, annualised, volatility, sharpe, max_drawdown)


def run(
    prices: pd.DataFrame,
    weights: dict[str, float],
    *,
    benchmark: pd.Series,
    start: date,
    end: date,
    training_end: date | None = None,
    transaction_cost_bps: float = DEFAULT_TRANSACTION_COST_BPS,
    rebalance_days: int = REBALANCE_DAYS,
    benchmark_symbol: str = DEFAULT_BENCHMARK,
) -> BacktestResult:
    """Backtest a fixed target allocation with periodic rebalancing.

    `prices` is a date-indexed frame of adjusted closes, one column per symbol.
    `training_end` is the last date the models saw; supplying it enables the §19
    overlap check.
    """
    if training_end is not None and start <= training_end:
        raise BacktestError(
            f"The backtest period starts {start}, which is inside the model's training "
            f"data (through {training_end}). §19 requires a separate, non-overlapping "
            "period — otherwise this measures memorisation, not performance."
        )

    window = prices.loc[str(start) : str(end)].dropna(how="all")
    held = [symbol for symbol in weights if symbol in window.columns]
    if not held or window.empty:
        raise BacktestError(
            "No price history for the requested backtest window. Ingest more history "
            "or choose a later start date."
        )

    window = window[held].ffill().dropna()
    if len(window) < 2:
        raise BacktestError("The backtest window contains fewer than two trading days.")

    target = np.array([weights[symbol] for symbol in held], dtype=float)
    target = target / target.sum()

    returns = window.pct_change().dropna()
    cost_rate = transaction_cost_bps / 10_000.0

    # Start fully invested at the target, paying to establish the position — an
    # initial allocation is not free, and treating it as free flatters short windows.
    holdings = target.copy()
    portfolio_returns: list[float] = []
    total_costs = float(np.abs(target).sum() * cost_rate)
    value = 1.0 - total_costs
    rebalances = 0

    for step, (_, row) in enumerate(returns.iterrows(), start=1):
        daily = float(holdings @ row.to_numpy())
        value *= 1.0 + daily

        # Drift: yesterday's weights move with yesterday's returns.
        grown = holdings * (1.0 + row.to_numpy())
        holdings = grown / grown.sum() if grown.sum() > 0 else target.copy()

        cost = 0.0
        if step % rebalance_days == 0:
            turnover = float(np.abs(target - holdings).sum())
            cost = turnover * cost_rate
            total_costs += cost
            value *= 1.0 - cost
            holdings = target.copy()
            rebalances += 1

        portfolio_returns.append(daily - cost)

    series = pd.Series(portfolio_returns, index=returns.index)
    benchmark_returns = benchmark.reindex(window.index).ffill().pct_change().dropna()

    return BacktestResult(
        start=window.index[0].date(),
        end=window.index[-1].date(),
        rebalances=rebalances,
        portfolio=compute_metrics(series),
        benchmark=compute_metrics(benchmark_returns),
        benchmark_symbol=benchmark_symbol,
        transaction_cost_bps=transaction_cost_bps,
        total_costs=round(total_costs, 6),
        equity_curve=(1.0 + series).cumprod(),
    )
