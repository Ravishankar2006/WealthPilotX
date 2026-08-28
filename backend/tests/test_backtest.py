"""§19 — backtesting: metrics, the overlap guard, and cost accounting."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.ml import backtest


@pytest.fixture
def prices() -> pd.DataFrame:
    """Three years of two assets plus a benchmark, deterministic."""
    rng = np.random.default_rng(7)
    index = pd.bdate_range("2023-01-02", periods=750)
    frame = {}
    for name, drift, vol in [
        ("AAA", 0.0004, 0.011),
        ("BBB", 0.0002, 0.006),
        ("SPY", 0.0003, 0.009),
    ]:
        frame[name] = 100 * np.exp(np.cumsum(rng.normal(drift, vol, len(index))))
    return pd.DataFrame(frame, index=index)


WEIGHTS = {"AAA": 0.6, "BBB": 0.4}


class TestMetrics:
    def test_all_five_section_19_metrics_are_produced(self) -> None:
        returns = pd.Series(np.full(252, 0.0004))
        metrics = backtest.compute_metrics(returns).as_dict()
        for key in (
            "total_return",
            "annualised_return",
            "volatility",
            "sharpe_ratio",
            "max_drawdown",
        ):
            assert key in metrics

    def test_a_steady_riser_has_no_drawdown(self) -> None:
        metrics = backtest.compute_metrics(pd.Series(np.full(252, 0.001)))
        assert metrics.max_drawdown == pytest.approx(0.0, abs=1e-9)
        assert metrics.total_return > 0

    def test_drawdown_is_measured_from_the_running_peak(self) -> None:
        # Up 20%, then down 50% from there.
        returns = pd.Series([0.2, -0.5])
        assert backtest.compute_metrics(returns).max_drawdown == pytest.approx(-0.5, abs=1e-9)

    def test_sharpe_uses_a_non_zero_risk_free_rate(self) -> None:
        """A zero rate silently flatters every Sharpe figure."""
        assert backtest.RISK_FREE_RATE > 0

    def test_an_empty_series_returns_zeros_rather_than_raising(self) -> None:
        assert backtest.compute_metrics(pd.Series(dtype=float)).total_return == 0.0

    def test_a_total_loss_does_not_produce_a_complex_annualised_return(self) -> None:
        """A fractional power of a negative number is complex; guard it."""
        metrics = backtest.compute_metrics(pd.Series([-1.0] + [0.0] * 100))
        assert isinstance(metrics.annualised_return, float)
        assert np.isfinite(metrics.annualised_return)


class TestOverlapGuard:
    def test_a_backtest_inside_the_training_window_is_refused(self, prices) -> None:
        """§19's core requirement. A backtest over data the model was fitted on
        measures memorisation, and would produce the most flattering number in the
        system."""
        with pytest.raises(backtest.BacktestError, match="non-overlapping"):
            backtest.run(
                prices,
                WEIGHTS,
                benchmark=prices["SPY"],
                start=date(2024, 1, 2),
                end=date(2025, 1, 2),
                training_end=date(2024, 6, 1),
            )

    def test_a_disjoint_period_is_allowed(self, prices) -> None:
        result = backtest.run(
            prices,
            WEIGHTS,
            benchmark=prices["SPY"],
            start=date(2025, 1, 2),
            end=date(2025, 12, 31),
            training_end=date(2024, 12, 31),
        )
        assert result.start >= date(2025, 1, 2)


class TestRun:
    def test_it_reports_the_portfolio_and_the_benchmark(self, prices) -> None:
        """§19 requires the comparison. Omitting it when unflattering is the exact
        failure mode the requirement exists to prevent."""
        result = backtest.run(
            prices, WEIGHTS, benchmark=prices["SPY"], start=date(2025, 1, 2), end=date(2025, 12, 31)
        )
        assert result.benchmark_symbol == "SPY"
        assert result.benchmark.total_return != 0.0
        assert result.portfolio.total_return != 0.0

    def test_transaction_costs_are_applied_and_reported(self, prices) -> None:
        """§19 was amended specifically so results are not misleadingly
        frictionless."""
        free = backtest.run(
            prices,
            WEIGHTS,
            benchmark=prices["SPY"],
            start=date(2025, 1, 2),
            end=date(2025, 12, 31),
            transaction_cost_bps=0.0,
        )
        costly = backtest.run(
            prices,
            WEIGHTS,
            benchmark=prices["SPY"],
            start=date(2025, 1, 2),
            end=date(2025, 12, 31),
            transaction_cost_bps=50.0,
        )

        assert costly.total_costs > free.total_costs
        assert costly.portfolio.total_return < free.portfolio.total_return
        assert costly.transaction_cost_bps == 50.0

    def test_rebalancing_happens_on_the_stated_cadence(self, prices) -> None:
        result = backtest.run(
            prices, WEIGHTS, benchmark=prices["SPY"], start=date(2025, 1, 2), end=date(2025, 12, 31)
        )
        assert result.rebalances > 0

    def test_a_window_with_no_data_is_an_error_not_an_empty_result(self, prices) -> None:
        with pytest.raises(backtest.BacktestError):
            backtest.run(
                prices,
                WEIGHTS,
                benchmark=prices["SPY"],
                start=date(2019, 1, 1),
                end=date(2019, 6, 1),
            )

    def test_unknown_symbols_in_the_weights_are_ignored_not_fatal(self, prices) -> None:
        result = backtest.run(
            prices,
            {"AAA": 0.5, "BBB": 0.3, "DELISTED": 0.2},
            benchmark=prices["SPY"],
            start=date(2025, 1, 2),
            end=date(2025, 12, 31),
        )
        # The surviving weights are renormalised, so the run is still fully invested.
        assert result.portfolio.total_return != 0.0

    def test_the_equity_curve_covers_the_window(self, prices) -> None:
        result = backtest.run(
            prices, WEIGHTS, benchmark=prices["SPY"], start=date(2025, 1, 2), end=date(2025, 12, 31)
        )
        assert len(result.equity_curve) > 200
        assert result.equity_curve.index[0].date() >= date(2025, 1, 2)
