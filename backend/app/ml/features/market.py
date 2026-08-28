"""Assembles the per-asset feature matrix (FR-07) and the FR-08 training target.

The one rule that governs this module: a row's features describe the past, and its
target describes the future. Everything about the horizon, the purge and the warm-up
drop exists to keep that true — get it wrong and the metrics are fiction.
"""

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ml.features import technical
from app.models.asset import Asset
from app.models.economic_indicator import EconomicIndicator
from app.models.market_data import MarketData

# The market proxy for correlation features. SPY is the broadest liquid US equity
# ETF in the M2 universe; if it is absent, correlation features are simply omitted
# rather than the whole pipeline failing.
BENCHMARK_SYMBOL = "SPY"

# Decision 2 of the phase plan: 20 trading days ≈ one month.
PREDICTION_HORIZON_DAYS = 20

# Feature columns, in a fixed order. The order is part of the model contract: a
# fitted booster indexes columns positionally, so a reordering here silently feeds
# volatility into the slot RSI trained on.
#
# Split into two groups because they have different availability guarantees.
# Market features are computable from price history alone, which every tracked
# asset has by definition.
MARKET_FEATURE_COLUMNS: tuple[str, ...] = (
    "sma_ratio_20",
    "sma_ratio_50",
    "ema_ratio_12",
    "rsi_14",
    "macd_hist",
    "bollinger_position",
    "volatility_20",
    "volatility_60",
    "momentum_20",
    "momentum_60",
    "return_lag_1",
    "return_lag_5",
    "return_lag_10",
    "return_lag_20",
    "benchmark_correlation_60",
    "volume_ratio_20",
)

# Macro features depend on FR-05 ingestion having run and on FRED being reachable.
# Treating them as mandatory made the entire feature matrix empty whenever
# `economic_indicators` was — which meant training silently produced nothing on any
# deployment where the economic job had not run yet. Market prediction must not be
# impossible because a second, independent data source is missing.
MACRO_FEATURE_COLUMNS: tuple[str, ...] = (
    "inflation",
    "interest_rate",
    "unemployment",
)

FEATURE_COLUMNS: tuple[str, ...] = MARKET_FEATURE_COLUMNS + MACRO_FEATURE_COLUMNS

TARGET_COLUMN = "forward_return_20"


def usable_feature_columns(frame: pd.DataFrame) -> tuple[str, ...]:
    """The declared features minus any macro column with no data at all.

    A column that is entirely NaN carries no information and, worse, would drop
    every row when the warm-up rows are trimmed. Dropping the column instead of the
    data is the only behaviour that degrades sensibly: the model trains on what is
    actually available, and records which columns those were so inference uses the
    same set.

    Market features are never dropped — a market feature that is entirely NaN means
    the price history is too short, which is a per-asset exclusion, not a per-column
    one.
    """
    macro = tuple(
        column
        for column in MACRO_FEATURE_COLUMNS
        if column in frame.columns and frame[column].notna().any()
    )
    return MARKET_FEATURE_COLUMNS + macro


@dataclass(frozen=True, slots=True)
class FeatureMatrix:
    """Features plus, when building a training set, the aligned target."""

    frame: pd.DataFrame
    symbol: str
    feature_columns: tuple[str, ...] = FEATURE_COLUMNS

    @property
    def features(self) -> pd.DataFrame:
        return self.frame[list(self.feature_columns)]

    @property
    def target(self) -> pd.Series:
        return self.frame[TARGET_COLUMN]


def load_prices(db: Session, symbol: str) -> pd.DataFrame:
    """Ascending OHLCV for one symbol, indexed by date."""
    statement = (
        select(MarketData.date, MarketData.adj_close, MarketData.close, MarketData.volume)
        .join(Asset, Asset.id == MarketData.asset_id)
        .where(Asset.symbol == symbol.upper())
        .order_by(MarketData.date)
    )
    rows = db.execute(statement).all()
    frame = pd.DataFrame(rows, columns=["date", "adj_close", "close", "volume"])
    if frame.empty:
        return frame

    frame["date"] = pd.to_datetime(frame["date"])
    # Decimal → float only here, at the boundary into numeric code. Storage and
    # transport stay exact; the model is indifferent to the last significant digit.
    for column in ("adj_close", "close", "volume"):
        frame[column] = frame[column].astype(float)
    return frame.set_index("date").sort_index()


def load_economic_series(db: Session) -> pd.DataFrame:
    """Macro series pivoted to one column each, indexed by observation date."""
    rows = db.execute(
        select(EconomicIndicator.series, EconomicIndicator.date, EconomicIndicator.value).order_by(
            EconomicIndicator.date
        )
    ).all()
    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows, columns=["series", "date", "value"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame["value"] = frame["value"].astype(float)
    pivoted = frame.pivot_table(index="date", columns="series", values="value", aggfunc="last")
    pivoted.columns = [str(column).lower() for column in pivoted.columns]
    return pivoted.sort_index()


def _attach_economic(frame: pd.DataFrame, economic: pd.DataFrame) -> pd.DataFrame:
    """Align monthly macro data onto daily rows by forward fill.

    Forward fill, never interpolate. A CPI figure published for January is the most
    recent *known* value every day until February's release; interpolating between
    them would place a number in the feature matrix before it existed, which is
    look-ahead leakage wearing a respectable disguise.
    """
    for column in ("inflation", "interest_rate", "unemployment"):
        if economic.empty or column not in economic.columns:
            frame[column] = np.nan
            continue
        frame[column] = economic[column].reindex(frame.index, method="ffill")
    return frame


def build_features(
    prices: pd.DataFrame, benchmark: pd.DataFrame | None, economic: pd.DataFrame
) -> pd.DataFrame:
    """Compute every FR-07 feature from a price frame."""
    close = prices["adj_close"]
    frame = pd.DataFrame(index=prices.index)

    # Ratios to price rather than raw levels: a $400 SMA and a $9 SMA are not
    # comparable features, but "3% above its 20-day average" is the same statement
    # about either. One model can then span the whole universe.
    frame["sma_ratio_20"] = close / technical.sma(close, 20) - 1
    frame["sma_ratio_50"] = close / technical.sma(close, 50) - 1
    frame["ema_ratio_12"] = close / technical.ema(close, 12) - 1

    frame["rsi_14"] = technical.rsi(close, 14)

    _, _, histogram = technical.macd(close)
    # Normalised by price for the same reason as the ratios above.
    frame["macd_hist"] = histogram / close

    frame["bollinger_position"] = technical.bollinger_position(close)

    frame["volatility_20"] = technical.volatility(close, 20)
    frame["volatility_60"] = technical.volatility(close, 60)

    frame["momentum_20"] = technical.momentum(close, 20)
    frame["momentum_60"] = technical.momentum(close, 60)

    lags = technical.lagged_returns(close)
    for column in lags.columns:
        frame[column] = lags[column]

    if benchmark is not None and not benchmark.empty:
        frame["benchmark_correlation_60"] = technical.rolling_correlation(
            close, benchmark["adj_close"], 60
        )
    else:
        frame["benchmark_correlation_60"] = np.nan

    volume = prices["volume"]
    average_volume = volume.rolling(window=20, min_periods=20).mean()
    frame["volume_ratio_20"] = (volume / average_volume).where(average_volume > 0)

    return _attach_economic(frame, economic)


def add_target(frame: pd.DataFrame, prices: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Attach the forward log return the model is trained to predict.

    `shift(-horizon)` is the only forward-looking operation in the pipeline, and it
    belongs only to the target. The last `horizon` rows have no future to look at and
    become NaN — dropped by the caller for training, and kept for inference, since
    those are exactly the rows we want a prediction for.
    """
    close = prices["adj_close"]
    frame[TARGET_COLUMN] = np.log(close.shift(-horizon) / close)
    return frame


def build_training_matrix(
    db: Session, symbol: str, *, horizon: int = PREDICTION_HORIZON_DAYS
) -> FeatureMatrix:
    """Feature matrix with target, warm-up and unlabelled tail rows removed."""
    prices = load_prices(db, symbol)
    if prices.empty:
        return FeatureMatrix(pd.DataFrame(columns=[*FEATURE_COLUMNS, TARGET_COLUMN]), symbol)

    benchmark = load_prices(db, BENCHMARK_SYMBOL) if symbol.upper() != BENCHMARK_SYMBOL else prices
    frame = build_features(prices, benchmark, load_economic_series(db))
    frame = add_target(frame, prices, horizon)

    columns = usable_feature_columns(frame)

    # Drop warm-up rows (indicators not yet defined) and the unlabelled tail. This is
    # what delivers FR-06's "zero nulls in required model-input columns" — the
    # alternative, imputing a mean, would fabricate history the asset never had.
    #
    # Only the *market* features and the target are required. Macro series are
    # published over shorter histories than price data, and requiring them silently
    # truncated training to the macro window: three years of prices became ten
    # months of usable rows, and every asset then fell below the minimum. XGBoost
    # handles missing values natively by learning a default split direction, so a
    # NaN macro column on older rows costs a little signal there and nothing
    # elsewhere. Imputing a value would be the harmful option — it would state a
    # CPI figure for a date before one existed.
    required = [*MARKET_FEATURE_COLUMNS, TARGET_COLUMN]
    kept = [*columns, TARGET_COLUMN]
    return FeatureMatrix(frame.dropna(subset=required)[kept], symbol, columns)


def build_inference_row(
    db: Session,
    symbol: str,
    *,
    as_of: date | None = None,
    feature_columns: tuple[str, ...] | None = None,
) -> tuple[pd.DataFrame, date] | None:
    """The single most recent complete feature row for one asset.

    Returns None when the asset has too little history for the indicators to be
    defined — a newly tracked symbol, most often. FR-09 requires metrics to be
    "returned or explicitly marked unavailable with a reason", so the caller reports
    that rather than serving a row full of imputed values.
    """
    prices = load_prices(db, symbol)
    if prices.empty:
        return None

    if as_of is not None:
        prices = prices[prices.index <= pd.Timestamp(as_of)]
        if prices.empty:
            return None

    benchmark = load_prices(db, BENCHMARK_SYMBOL) if symbol.upper() != BENCHMARK_SYMBOL else prices
    frame = build_features(prices, benchmark, load_economic_series(db))

    # The trained model dictates the columns — it was fitted on a specific set, and
    # a macro column that has since arrived must not shift the feature positions the
    # booster indexes by. Falls back to whatever is usable when no model is named.
    columns = tuple(feature_columns) if feature_columns else usable_feature_columns(frame)
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        return None

    # As in training: market features must be present, macro may be missing.
    required = [column for column in columns if column in MARKET_FEATURE_COLUMNS]
    complete = frame.dropna(subset=required)
    if complete.empty:
        return None

    last = complete.iloc[[-1]][list(columns)]
    return last, complete.index[-1].date()
