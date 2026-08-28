"""Training-set assembly and leakage-safe splitting for FR-08.

The single most important thing in this phase. A 20-day forward return means each
row's target overlaps the next 19 rows' targets, so a random train/test split puts
near-identical samples on both sides and reports an R² that is fiction. Two defences,
in order:

1. **Chronological split.** Test data comes strictly after training data — the only
   arrangement that resembles how the model will actually be used.
2. **A purge gap of one full horizon.** Even a chronological split leaks at the seam:
   the last training row's target reaches 20 days forward, into the test period. The
   gap drops those rows entirely.

§18 requires the split methodology and leakage checks to be published. This module
is that methodology, and `test_leakage.py` is the check.
"""

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ml.features.market import (
    FEATURE_COLUMNS,
    MARKET_FEATURE_COLUMNS,
    PREDICTION_HORIZON_DAYS,
    TARGET_COLUMN,
    build_training_matrix,
)
from app.models.asset import Asset

# Below this, a per-asset matrix is too short for a chronological split to leave a
# meaningful test period. Roughly a year of sessions after the indicator warm-up.
MIN_ROWS_PER_ASSET = 250

TEST_FRACTION = 0.2


@dataclass(frozen=True, slots=True)
class TrainingData:
    x_train: pd.DataFrame
    y_train: pd.Series
    x_test: pd.DataFrame
    y_test: pd.Series
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    symbols: list[str]
    # Which features this data actually carries. Macro columns are dropped when the
    # economic tables are empty, so the set is resolved per run and travels with the
    # fitted model rather than being assumed.
    feature_columns: tuple[str, ...] = FEATURE_COLUMNS

    @property
    def is_empty(self) -> bool:
        return self.x_train.empty or self.x_test.empty


def build_pooled_matrix(db: Session, symbols: list[str] | None = None) -> pd.DataFrame:
    """One matrix across all tracked assets, with the date kept as a column.

    Pooled rather than one model per symbol: a per-asset model gets a few hundred
    rows and overfits, while the features are deliberately scale-free (ratios and
    returns, never price levels) precisely so one model can span the universe.
    """
    if symbols is None:
        symbols = [row for row in db.scalars(select(Asset.symbol).where(Asset.is_active.is_(True)))]

    frames: list[pd.DataFrame] = []
    common: set[str] | None = None

    for symbol in sorted(symbols):
        matrix = build_training_matrix(db, symbol)
        if len(matrix.frame) < MIN_ROWS_PER_ASSET:
            continue
        frame = matrix.frame.copy()
        frame["symbol"] = symbol
        frame["date"] = frame.index
        frames.append(frame)
        # Intersect rather than union: a column present for only some assets would
        # be NaN for the rest, and pooling is the whole point of this matrix.
        common = (
            set(matrix.feature_columns) if common is None else common & set(matrix.feature_columns)
        )

    if not frames:
        return pd.DataFrame(columns=[*FEATURE_COLUMNS, TARGET_COLUMN, "symbol", "date"])

    columns = [column for column in FEATURE_COLUMNS if column in (common or set())]
    pooled = pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)
    return pooled[[*columns, TARGET_COLUMN, "symbol", "date"]]


def chronological_split(
    frame: pd.DataFrame,
    *,
    test_fraction: float = TEST_FRACTION,
    purge_days: int = PREDICTION_HORIZON_DAYS,
) -> TrainingData:
    """Split by date with a purge gap, never by row index.

    Splitting on a date rather than a row position matters for a pooled matrix: 32
    assets contribute rows for the same day, and an index split would cut through the
    middle of a date, putting one asset's day in train and another's in test.
    """
    # Resolved from what the matrix actually carries: macro columns are dropped when
    # the economic tables are empty, so the set travels with the data rather than
    # being assumed from the declared list.
    columns = tuple(column for column in FEATURE_COLUMNS if column in frame.columns)

    if frame.empty or not columns:
        columns = columns or MARKET_FEATURE_COLUMNS
        empty = pd.DataFrame(columns=list(columns))
        blank = pd.Timestamp("1970-01-01")
        return TrainingData(
            empty,
            pd.Series(dtype=float),
            empty,
            pd.Series(dtype=float),
            blank,
            blank,
            blank,
            blank,
            [],
            columns,
        )

    dates = pd.Series(sorted(frame["date"].unique()))
    cutoff = dates.iloc[int(len(dates) * (1 - test_fraction))]

    # The purge: training rows whose target window reaches into the test period are
    # dropped, rather than merely being separated from it.
    train_ceiling = cutoff - pd.Timedelta(days=purge_days * 2)

    train = frame[frame["date"] <= train_ceiling]
    test = frame[frame["date"] > cutoff]

    return TrainingData(
        x_train=train[list(columns)],
        y_train=train[TARGET_COLUMN],
        x_test=test[list(columns)],
        y_test=test[TARGET_COLUMN],
        train_start=train["date"].min() if not train.empty else cutoff,
        train_end=train["date"].max() if not train.empty else cutoff,
        test_start=test["date"].min() if not test.empty else cutoff,
        test_end=test["date"].max() if not test.empty else cutoff,
        symbols=sorted(frame["symbol"].unique().tolist()),
        feature_columns=columns,
    )


def build_training_data(
    db: Session, symbols: list[str] | None = None, *, holdout_days: int = 0
) -> TrainingData:
    """Assemble and split. `holdout_days` reserves the most recent period entirely.

    §19 requires a backtest period separate from the training period, and a model
    trained through yesterday leaves none. Reserving the tail here is what makes a
    genuine out-of-sample backtest possible rather than theoretically required.
    """
    frame = build_pooled_matrix(db, symbols)
    if holdout_days > 0 and not frame.empty:
        cutoff = frame["date"].max() - pd.Timedelta(days=holdout_days)
        frame = frame[frame["date"] <= cutoff]
    return chronological_split(frame)
