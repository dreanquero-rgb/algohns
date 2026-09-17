"""Walk-forward causality, pinned.

`Backtester.run()` applies a fixed weight vector across the whole sample.
When those weights came from `PortfolioOptimizer` fitted on that same
sample — which is how the page used to call it — the curve is in-sample and
overstates what was achievable.

`run_walk_forward` re-optimises at each rebalance on data available then.
These tests prove it: they perturb the tail of the price history and assert
the earlier portion of the return series is bit-identical. A leak anywhere
in the estimation chain fails them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from algohns.modules.backtest_suite import Backtester


def _prices(n: int = 900, seed: int = 7) -> pd.DataFrame:
    """Correlated random walks: enough structure for an optimiser to bite on."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    names = ["AAA", "BBB", "CCC", "DDD"]
    common = rng.standard_normal((n, 1))
    idio = rng.standard_normal((n, len(names)))
    shocks = 0.6 * common + 0.8 * idio
    vols = np.array([0.018, 0.012, 0.009, 0.020])
    drift = np.array([0.0005, 0.0004, 0.0003, 0.0006])
    paths = 100 * np.cumprod(1 + drift + shocks * vols, axis=0)
    return pd.DataFrame(paths, index=idx, columns=names)


class TestWalkForwardIsCausal:
    def test_future_prices_cannot_change_past_returns(self):
        """Triple every price in the last 20%; earlier returns must not move."""
        px = _prices()
        cut = int(len(px) * 0.8)
        tampered = px.copy()
        tampered.iloc[cut:] *= 3.0

        base = Backtester(px).run_walk_forward("min_volatility", lookback=252,
                                               min_lookback=252)
        after = Backtester(tampered).run_walk_forward("min_volatility", lookback=252,
                                                      min_lookback=252)
        split = px.index[cut - 1]
        a = base.equity_curve.loc[:split]
        b = after.equity_curve.loc[:split]
        pd.testing.assert_series_equal(a, b)

    def test_in_sample_run_does_leak(self):
        """Documents the contrast: the fixed-weight path is not causal.

        Not a defect in `run()` itself — it takes weights as an argument —
        but it is why the page defaults to walk-forward and labels the
        other mode.
        """
        from algohns.modules.backtest_suite import PortfolioOptimizer

        px = _prices()
        cut = int(len(px) * 0.8)
        tampered = px.copy()
        tampered.iloc[cut:] *= 3.0

        w_base = PortfolioOptimizer(px).optimize("min_volatility")
        w_after = PortfolioOptimizer(tampered).optimize("min_volatility")
        # Weights fitted on the full sample react to the tampered tail.
        assert w_base != pytest.approx(w_after)


class TestWalkForwardBehaviour:
    def test_produces_a_curve(self):
        r = Backtester(_prices()).run_walk_forward("max_sharpe", min_lookback=252)
        assert len(r.equity_curve) == 900
        assert r.equity_curve.iloc[0] > 0
        assert r.weights

    def test_weights_sum_to_one(self):
        r = Backtester(_prices()).run_walk_forward("max_sharpe", min_lookback=252)
        assert sum(r.weights.values()) == pytest.approx(1.0, abs=1e-6)

    def test_costs_reduce_return(self):
        px = _prices()
        free = Backtester(px).run_walk_forward("max_sharpe", min_lookback=252,
                                               cost_bps=0.0)
        pricey = Backtester(px).run_walk_forward("max_sharpe", min_lookback=252,
                                                 cost_bps=100.0)
        assert pricey.equity_curve.iloc[-1] < free.equity_curve.iloc[-1]

    def test_more_frequent_rebalancing_costs_more(self):
        px = _prices()
        monthly = Backtester(px).run_walk_forward("max_sharpe", rebalance="M",
                                                  min_lookback=252, cost_bps=50.0)
        yearly = Backtester(px).run_walk_forward("max_sharpe", rebalance="Y",
                                                 min_lookback=252, cost_bps=50.0)
        assert monthly.equity_curve.iloc[-1] < yearly.equity_curve.iloc[-1]

    def test_short_history_rejected(self):
        with pytest.raises(ValueError, match="Not enough history"):
            Backtester(_prices(n=100)).run_walk_forward("max_sharpe",
                                                        min_lookback=252)

    def test_drawdown_never_positive(self):
        r = Backtester(_prices()).run_walk_forward("min_volatility", min_lookback=252)
        assert r.drawdown_curve.max() <= 1e-12
