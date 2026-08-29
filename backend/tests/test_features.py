"""FR-07 indicators, and the leakage checks that make their metrics believable.

The leakage tests are the ones that matter. An indicator that peeks at future data
inflates every downstream number and produces a model that evaluates beautifully and
predicts nothing — and it is invisible to every other kind of test.
"""

import numpy as np
import pandas as pd
import pytest

from app.ml.features import technical
from app.ml.features.market import (
    FEATURE_COLUMNS,
    MACRO_FEATURE_COLUMNS,
    MARKET_FEATURE_COLUMNS,
    TARGET_COLUMN,
    add_target,
    build_features,
    usable_feature_columns,
)


def _economic_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Macro series covering the price window, so every declared feature is usable."""
    return pd.DataFrame(
        {"inflation": [300.0], "interest_rate": [4.5], "unemployment": [4.0]},
        index=pd.to_datetime([index[0]]),
    )


@pytest.fixture
def prices() -> pd.DataFrame:
    """250 sessions of a deterministic wandering series."""
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2025-01-01", periods=250)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.012, len(dates))))
    return pd.DataFrame(
        {"adj_close": close, "close": close, "volume": rng.integers(1e6, 5e6, len(dates))},
        index=dates,
    )


class TestIndicatorCorrectness:
    def test_sma_matches_a_hand_computed_mean(self) -> None:
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = technical.sma(series, 3)
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_leading_values_are_nan_not_zero(self) -> None:
        """A 20-day average has no value on day 3. Filling that with 0 invents a
        data point and teaches the model a discontinuity that never happened."""
        result = technical.sma(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), 3)
        assert result.iloc[:2].isna().all()

    def test_rsi_stays_within_its_bounds(self, prices: pd.DataFrame) -> None:
        result = technical.rsi(prices["adj_close"], 14).dropna()
        assert result.between(0, 100).all()

    def test_rsi_is_100_when_every_period_gains(self) -> None:
        """An all-gains window gives an infinite RS. 100 is the boundary; NaN would
        be a hole in the feature matrix."""
        result = technical.rsi(pd.Series(np.arange(1.0, 40.0)), 14).dropna()
        assert (result == 100.0).all()

    def test_bollinger_bands_are_ordered(self, prices: pd.DataFrame) -> None:
        lower, middle, upper = technical.bollinger(prices["adj_close"])
        frame = pd.DataFrame({"lower": lower, "middle": middle, "upper": upper}).dropna()
        assert (frame["lower"] <= frame["middle"]).all()
        assert (frame["middle"] <= frame["upper"]).all()

    def test_bollinger_position_is_scale_free(self) -> None:
        """The same shape at $9 and $400 must give the same feature value — one
        model spans the whole universe."""
        rng = np.random.default_rng(7)
        base = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 100))))
        cheap = technical.bollinger_position(base).dropna()
        expensive = technical.bollinger_position(base * 40).dropna()
        assert np.allclose(cheap.to_numpy(), expensive.to_numpy())

    def test_macd_histogram_is_the_line_minus_its_signal(self, prices: pd.DataFrame) -> None:
        line, signal, histogram = technical.macd(prices["adj_close"])
        assert np.allclose((line - signal).dropna(), histogram.dropna())

    def test_volatility_is_annualised_by_252(self, prices: pd.DataFrame) -> None:
        daily = technical.volatility(prices["adj_close"], 20, annualise=False).dropna()
        annual = technical.volatility(prices["adj_close"], 20, annualise=True).dropna()
        assert np.allclose(annual.to_numpy(), daily.to_numpy() * np.sqrt(252))

    def test_momentum_is_the_trailing_return(self) -> None:
        series = pd.Series([100.0] * 20 + [110.0])
        assert technical.momentum(series, 20).iloc[-1] == pytest.approx(0.10)

    def test_correlation_with_itself_is_one(self, prices: pd.DataFrame) -> None:
        result = technical.rolling_correlation(
            prices["adj_close"], prices["adj_close"], 60
        ).dropna()
        assert np.allclose(result.to_numpy(), 1.0)


class TestNoLookAhead:
    """Every indicator's value at t must depend only on data at or before t."""

    @pytest.mark.parametrize(
        "compute",
        [
            pytest.param(lambda s: technical.sma(s, 20), id="sma"),
            pytest.param(lambda s: technical.ema(s, 12), id="ema"),
            pytest.param(lambda s: technical.rsi(s, 14), id="rsi"),
            pytest.param(lambda s: technical.macd(s)[2], id="macd_histogram"),
            pytest.param(lambda s: technical.bollinger(s)[2], id="bollinger_upper"),
            pytest.param(lambda s: technical.bollinger_position(s), id="bollinger_position"),
            pytest.param(lambda s: technical.volatility(s, 20), id="volatility"),
            pytest.param(lambda s: technical.momentum(s, 20), id="momentum"),
            pytest.param(lambda s: technical.log_returns(s), id="log_returns"),
            pytest.param(lambda s: technical.lagged_returns(s)["return_lag_5"], id="lag_5"),
        ],
    )
    def test_appending_future_data_does_not_change_earlier_values(
        self, prices: pd.DataFrame, compute
    ) -> None:
        close = prices["adj_close"]
        truncated = compute(close.iloc[:200])
        full = compute(close)

        overlap = truncated.dropna()
        assert not overlap.empty, "indicator produced no values to compare"
        assert np.allclose(overlap.to_numpy(), full.loc[overlap.index].to_numpy())


class TestFeatureAssembly:
    def test_every_declared_feature_column_is_produced(self, prices: pd.DataFrame) -> None:
        frame = build_features(prices, prices, pd.DataFrame())
        for column in FEATURE_COLUMNS:
            assert column in frame.columns, f"{column} missing from the feature matrix"

    def test_the_target_looks_forward_and_the_features_do_not(self, prices: pd.DataFrame) -> None:
        """The one asymmetry the whole pipeline depends on."""
        frame = add_target(build_features(prices, prices, pd.DataFrame()), prices, 20)

        close = prices["adj_close"]
        expected = np.log(close.iloc[40 + 20] / close.iloc[40])
        assert frame[TARGET_COLUMN].iloc[40] == pytest.approx(expected)

    def test_the_last_horizon_rows_have_no_target(self, prices: pd.DataFrame) -> None:
        """They have no future to look at — and they are exactly the rows inference
        wants, which is why they are dropped for training but kept for prediction."""
        frame = add_target(build_features(prices, prices, pd.DataFrame()), prices, 20)
        assert frame[TARGET_COLUMN].iloc[-20:].isna().all()

    def test_dropping_warm_up_rows_leaves_no_nulls(self, prices: pd.DataFrame) -> None:
        """FR-06: zero nulls in required model-input columns."""
        economic = _economic_frame(prices.index)
        frame = add_target(build_features(prices, prices, economic), prices, 20)

        columns = usable_feature_columns(frame)
        usable = frame.dropna(subset=[*columns, TARGET_COLUMN])

        assert not usable.empty
        assert not usable[list(columns)].isna().to_numpy().any()
        # With macro data present, every declared column is in play.
        assert set(columns) == set(FEATURE_COLUMNS)

    def test_missing_macro_data_drops_the_column_not_every_row(self, prices: pd.DataFrame) -> None:
        """Regression, and a real production defect.

        Macro features were mandatory, so an empty `economic_indicators` table made
        `dropna` discard the entire feature matrix — training silently produced
        nothing on any deployment where the FR-05 job had not run. Market prediction
        must not become impossible because a second, independent source is missing.
        """
        frame = add_target(build_features(prices, prices, pd.DataFrame()), prices, 20)
        columns = usable_feature_columns(frame)

        assert set(columns) == set(MARKET_FEATURE_COLUMNS)
        assert not set(columns) & set(MACRO_FEATURE_COLUMNS)

        usable = frame.dropna(subset=[*columns, TARGET_COLUMN])
        assert not usable.empty, "an empty economic table emptied the whole matrix"

    def test_partial_macro_data_keeps_the_series_that_exist(self, prices: pd.DataFrame) -> None:
        economic = pd.DataFrame({"inflation": [300.0]}, index=pd.to_datetime([prices.index[0]]))
        frame = build_features(prices, prices, economic)
        columns = usable_feature_columns(frame)

        assert "inflation" in columns
        assert "unemployment" not in columns

    def test_macro_data_is_forward_filled_never_interpolated(self) -> None:
        """Interpolating between two CPI releases places a number in the feature
        matrix before it existed — look-ahead leakage in a respectable disguise."""
        dates = pd.bdate_range("2025-01-01", periods=60)
        prices = pd.DataFrame(
            {
                "adj_close": np.linspace(100, 120, 60),
                "close": np.linspace(100, 120, 60),
                "volume": [1_000_000] * 60,
            },
            index=dates,
        )
        economic = pd.DataFrame(
            {"inflation": [300.0, 310.0]},
            index=pd.to_datetime(["2025-01-01", "2025-02-03"]),
        )
        frame = build_features(prices, prices, economic)

        january = frame.loc[frame.index < "2025-02-03", "inflation"].dropna()
        assert (january == 300.0).all(), "a value between releases was invented"


class TestBenchmarkAbsence:
    """§16.3 — a universe without SPY must degrade, not disappear.

    `BENCHMARK_SYMBOL`'s docstring said an absent benchmark meant "correlation
    features are simply omitted rather than the whole pipeline failing". It did not:
    `benchmark_correlation_60` sat in `MARKET_FEATURE_COLUMNS`, which
    `usable_feature_columns` never dropped, so an all-NaN correlation column took
    every row with it when the warm-up rows were trimmed. The documented behaviour
    and the actual behaviour had come apart; these tests pin the documented one.
    """

    def test_an_all_nan_correlation_column_is_dropped(self) -> None:
        frame = pd.DataFrame(
            {
                "sma_ratio_20": [0.01, 0.02],
                "benchmark_correlation_60": [np.nan, np.nan],
                "inflation": [3.1, 3.2],
            }
        )
        columns = usable_feature_columns(frame)

        assert "benchmark_correlation_60" not in columns
        assert "sma_ratio_20" in columns

    def test_a_populated_correlation_column_is_kept(self) -> None:
        frame = pd.DataFrame(
            {
                "sma_ratio_20": [0.01, 0.02],
                "benchmark_correlation_60": [np.nan, 0.8],
                "inflation": [3.1, 3.2],
            }
        )
        assert "benchmark_correlation_60" in usable_feature_columns(frame)
