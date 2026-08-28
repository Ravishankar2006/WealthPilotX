"""Technical indicators (FR-07).

Pure functions over an ordered price series. Two properties are load-bearing, and
both are tested rather than asserted in review:

* **No look-ahead.** The value at index `t` depends only on data at or before `t`.
  An indicator that peeks forward inflates every downstream metric and produces a
  model that looks excellent and predicts nothing. The test for this appends future
  data and checks that earlier values do not move.
* **Leading values are NaN, not zero.** A 20-day moving average has no value on day
  3. Filling that with 0 — or with the first price — invents a data point and
  teaches the model a discontinuity that does not exist. Callers drop the warm-up
  rows; `assemble` in `market.py` does exactly that.

Implemented directly rather than via a TA library: these are a few lines each, the
dependency surface stays small, and the exact NaN and smoothing conventions are
visible here instead of being someone else's default.
"""

import numpy as np
import pandas as pd


# Wilder's smoothing, which is what "RSI(14)" conventionally means. An EMA with
# alpha = 1/period, not the 2/(period+1) that `ewm(span=)` would give.
def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def sma(close: pd.Series, period: int) -> pd.Series:
    """Simple moving average. NaN until `period` observations exist."""
    return close.rolling(window=period, min_periods=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative strength index, in [0, 100].

    A period with no losses gives an infinite RS; the fillna below maps that to 100,
    which is the correct boundary rather than a NaN hole in the feature matrix.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = _wilder(gain, period)
    avg_loss = _wilder(loss, period)

    rs = avg_gain / avg_loss
    result = 100 - (100 / (1 + rs))
    # avg_loss == 0 → rs is inf → result is NaN by the arithmetic above, but the
    # meaning is "no losses at all", i.e. 100. Only fill where the inputs existed.
    return result.where(avg_loss != 0, other=100.0).where(avg_gain.notna(), other=np.nan)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, and histogram."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return macd_line, signal_line, macd_line - signal_line


def bollinger(
    close: pd.Series, period: int = 20, deviations: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Lower, middle and upper band. Population std, matching the usual convention."""
    middle = sma(close, period)
    spread = close.rolling(window=period, min_periods=period).std(ddof=0) * deviations
    return middle - spread, middle, middle + spread


def log_returns(close: pd.Series) -> pd.Series:
    """Log returns, which add across time and treat gains and losses symmetrically —
    the property that matters when the model is asked about downside."""
    return np.log(close / close.shift(1))


def volatility(close: pd.Series, period: int = 20, *, annualise: bool = True) -> pd.Series:
    """Realised volatility of log returns, annualised by default.

    252 trading days: the conventional US equity year, and the same constant the M4
    Sharpe calculation will use. Defined once here so the two cannot disagree.
    """
    daily = log_returns(close).rolling(window=period, min_periods=period).std(ddof=0)
    return daily * np.sqrt(252) if annualise else daily


def momentum(close: pd.Series, period: int = 20) -> pd.Series:
    """Trailing return over `period` days — price now against price then."""
    return close / close.shift(period) - 1


def lagged_returns(close: pd.Series, lags: tuple[int, ...] = (1, 5, 10, 20)) -> pd.DataFrame:
    """Past returns at several horizons, the model's view of recent history."""
    returns = log_returns(close)
    return pd.DataFrame(
        {f"return_lag_{lag}": returns.shift(lag - 1) for lag in lags}, index=close.index
    )


def rolling_correlation(
    close: pd.Series, benchmark_close: pd.Series, period: int = 60
) -> pd.Series:
    """Correlation of this asset's returns with a market proxy (FR-07).

    The diversification signal M4's optimizer needs: an asset that moves with
    everything else is worth less in a portfolio than its return alone suggests.
    """
    asset = log_returns(close)
    market = log_returns(benchmark_close.reindex(close.index))
    return asset.rolling(window=period, min_periods=period).corr(market)


def bollinger_position(close: pd.Series, period: int = 20, deviations: float = 2.0) -> pd.Series:
    """Where price sits within its bands: 0 at the lower band, 1 at the upper.

    Scale-free, so one model can span a $9 ETF and a $400 one — a raw band distance
    would make the feature mean something different for every asset.
    """
    lower, _, upper = bollinger(close, period, deviations)
    width = upper - lower
    return ((close - lower) / width).where(width > 0)
