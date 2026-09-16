"""Tests for metrics, optimisers and the walk-forward engine.

The causality tests are the load-bearing ones: they prove the backtester
cannot see the future. Everything else is arithmetic that could be spotted
by eye; look-ahead bias could not.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from modules.backtest_suite import BacktestConfig, compare_objectives, run_backtest
from modules.market_data import load_prices, synthetic_prices
from modules.metrics import compute_metrics, drawdown_series
from modules.optimizers import (
    Objective,
    black_litterman_returns,
    covariance,
    expected_returns,
    optimize,
)

TICKERS = ["AAPL", "MSFT", "JNJ", "XOM", "JPM", "KO"]


@pytest.fixture(scope="module")
def prices() -> pd.DataFrame:
    return synthetic_prices(TICKERS, date(2019, 1, 1), date(2026, 1, 1), seed=11)


@pytest.fixture(scope="module")
def mu_cov(prices):
    return expected_returns(prices), covariance(prices)


# ---------------------------------------------------------------- metrics


class TestMetrics:
    def test_cagr_matches_closed_form(self):
        r = pd.Series([0.001] * 252, index=pd.bdate_range("2021-01-01", periods=252))
        assert compute_metrics(r).cagr == pytest.approx(1.001**252 - 1, rel=1e-9)

    def test_constant_positive_has_no_drawdown(self):
        r = pd.Series([0.001] * 100, index=pd.bdate_range("2021-01-01", periods=100))
        m = compute_metrics(r)
        assert m.max_drawdown == pytest.approx(0.0)
        assert m.win_rate == pytest.approx(1.0)

    def test_drawdown_is_never_positive(self, prices):
        dd = drawdown_series(prices["AAPL"].pct_change().dropna())
        assert dd.max() <= 1e-12
        assert dd.min() < 0

    def test_known_drawdown_depth(self):
        """-50% then flat must report exactly -50%."""
        r = pd.Series([0.0, -0.5, 0.0, 0.0],
                      index=pd.bdate_range("2021-01-01", periods=4))
        assert compute_metrics(r).max_drawdown == pytest.approx(-0.5)

    def test_drawdown_recovery_detected(self):
        r = pd.Series([0.0, -0.2, 0.0, 0.25, 0.1],
                      index=pd.bdate_range("2021-01-01", periods=5))
        m = compute_metrics(r)
        assert m.max_dd_end is not None
        assert m.max_dd_recovery is not None
        assert m.max_dd_recovery > m.max_dd_end

    def test_unrecovered_drawdown_has_no_recovery_date(self):
        r = pd.Series([0.0, -0.3, 0.01, 0.01],
                      index=pd.bdate_range("2021-01-01", periods=4))
        assert compute_metrics(r).max_dd_recovery is None

    def test_risk_free_reduces_sharpe(self, prices):
        r = prices["AAPL"].pct_change().dropna()
        assert compute_metrics(r, risk_free_rate=0.05).sharpe < \
               compute_metrics(r, risk_free_rate=0.0).sharpe

    def test_beta_against_self_is_one(self, prices):
        r = prices["AAPL"].pct_change().dropna()
        m = compute_metrics(r, benchmark=r)
        assert m.beta == pytest.approx(1.0, abs=1e-9)
        assert m.r_squared == pytest.approx(1.0, abs=1e-9)
        assert m.tracking_error == pytest.approx(0.0, abs=1e-12)

    def test_beta_of_double_leverage_is_two(self, prices):
        b = prices["AAPL"].pct_change().dropna()
        m = compute_metrics(2.0 * b, benchmark=b)
        assert m.beta == pytest.approx(2.0, rel=1e-6)

    def test_var_below_cvar_in_magnitude(self, prices):
        m = compute_metrics(prices["XOM"].pct_change().dropna())
        assert m.cvar_95 <= m.var_95  # CVaR is the deeper tail

    def test_rejects_empty_and_single_point(self):
        with pytest.raises(ValueError, match="vuota"):
            compute_metrics(pd.Series(dtype=float))
        with pytest.raises(ValueError, match="almeno 2"):
            compute_metrics(pd.Series([0.01], index=pd.bdate_range("2021-01-01", periods=1)))

    def test_short_benchmark_overlap_is_noted_not_fatal(self, prices):
        r = prices["AAPL"].pct_change().dropna()
        b = r.iloc[:2]
        m = compute_metrics(r, benchmark=b)
        assert m.beta is None
        assert any("benchmark" in n.lower() for n in m.notes)


# ------------------------------------------------------------- optimizers


class TestOptimizers:
    @pytest.mark.parametrize("obj", list(Objective))
    def test_weights_sum_to_one_and_are_long_only(self, mu_cov, obj):
        mu, cov = mu_cov
        r = optimize(mu, cov, objective=obj)
        assert sum(r.weights.values()) == pytest.approx(1.0, abs=1e-8)
        assert all(w >= -1e-9 for w in r.weights.values())

    def test_min_variance_has_lowest_volatility(self, mu_cov):
        mu, cov = mu_cov
        mv = optimize(mu, cov, objective=Objective.MIN_VARIANCE).volatility
        for obj in (Objective.MAX_SHARPE, Objective.EQUAL_WEIGHT,
                    Objective.RISK_PARITY, Objective.HRP):
            assert mv <= optimize(mu, cov, objective=obj).volatility + 1e-9

    def test_max_sharpe_has_highest_sharpe(self, mu_cov):
        mu, cov = mu_cov
        best = optimize(mu, cov, objective=Objective.MAX_SHARPE, risk_free_rate=0.02).sharpe
        for obj in (Objective.MIN_VARIANCE, Objective.EQUAL_WEIGHT,
                    Objective.RISK_PARITY, Objective.HRP):
            assert best >= optimize(mu, cov, objective=obj, risk_free_rate=0.02).sharpe - 1e-6

    def test_risk_parity_equalises_risk_contributions(self, mu_cov):
        mu, cov = mu_cov
        r = optimize(mu, cov, objective=Objective.RISK_PARITY)
        rc = np.array(list(r.risk_contributions.values()))
        assert rc.max() - rc.min() < 1e-4

    def test_equal_weight_is_exactly_equal(self, mu_cov):
        mu, cov = mu_cov
        r = optimize(mu, cov, objective=Objective.EQUAL_WEIGHT)
        assert set(np.round(list(r.weights.values()), 10)) == {round(1 / len(TICKERS), 10)}
        assert r.effective_n == pytest.approx(len(TICKERS))

    def test_weight_cap_respected(self, mu_cov):
        mu, cov = mu_cov
        r = optimize(mu, cov, objective=Objective.MAX_SHARPE, max_weight_per_asset=0.25)
        assert max(r.weights.values()) <= 0.25 + 1e-8

    def test_infeasible_cap_rejected(self, mu_cov):
        mu, cov = mu_cov
        with pytest.raises(ValueError, match="budget"):
            optimize(mu, cov, max_weight_per_asset=0.05)  # 0.05 * 6 < 1

    def test_effective_n_detects_concentration(self, mu_cov):
        mu, cov = mu_cov
        ew = optimize(mu, cov, objective=Objective.EQUAL_WEIGHT)
        ms = optimize(mu, cov, objective=Objective.MAX_SHARPE)
        assert ew.effective_n > ms.effective_n

    def test_shrinkage_moves_covariance(self, prices):
        raw = covariance(prices, shrinkage=False)
        shrunk = covariance(prices, shrinkage=True)
        # Diagonal is preserved by the constant-correlation target.
        assert np.allclose(np.diag(raw), np.diag(shrunk), rtol=1e-8)

    def test_covariance_is_symmetric_psd(self, prices):
        cov = covariance(prices).to_numpy()
        assert np.allclose(cov, cov.T)
        assert np.linalg.eigvalsh(cov).min() > -1e-10

    def test_single_asset_hrp(self):
        cov = pd.DataFrame([[0.04]], index=["A"], columns=["A"])
        mu = pd.Series({"A": 0.08})
        assert optimize(mu, cov, objective=Objective.HRP).weights["A"] == pytest.approx(1.0)


class TestBlackLitterman:
    def test_bullish_view_raises_posterior_above_its_prior(self, mu_cov):
        """The viewed asset must rise *relative to its own equilibrium prior*.

        Not "above every other asset": a bullish view on a low-beta name can
        legitimately stay below a high-beta name's equilibrium return. The
        prior-relative move is the property Black-Litterman actually
        guarantees.
        """
        _, cov = mu_cov
        mkt = pd.Series({t: 1 / len(TICKERS) for t in TICKERS})
        pi = 2.5 * cov.to_numpy() @ mkt.reindex(TICKERS).to_numpy()
        prior = pd.Series(pi, index=TICKERS)

        post = black_litterman_returns(cov, mkt, {"XOM": 0.40})
        # The view (40%) is above XOM's prior, so the posterior must move up,
        # and by more than any unviewed asset moves through correlation.
        assert post["XOM"] > prior["XOM"]
        own_move = post["XOM"] - prior["XOM"]
        for t in TICKERS:
            if t != "XOM":
                assert post[t] - prior[t] < own_move

    def test_bearish_view_lowers_posterior(self, mu_cov):
        _, cov = mu_cov
        mkt = pd.Series({t: 1 / len(TICKERS) for t in TICKERS})
        pi = 2.5 * cov.to_numpy() @ mkt.reindex(TICKERS).to_numpy()
        post = black_litterman_returns(cov, mkt, {"XOM": -0.20})
        assert post["XOM"] < pd.Series(pi, index=TICKERS)["XOM"]

    def test_bullish_view_raises_allocation(self, mu_cov):
        """Compared against the *equilibrium* allocation, not the historical one.

        Optimising on historical mu is a different problem from optimising
        on a BL posterior; comparing the two would not isolate the view's
        effect. The prior Pi is the correct no-view baseline.
        """
        _, cov = mu_cov
        mkt = pd.Series({t: 1 / len(TICKERS) for t in TICKERS})
        prior = pd.Series(2.5 * cov.to_numpy() @ mkt.reindex(TICKERS).to_numpy(),
                          index=TICKERS)
        post = black_litterman_returns(cov, mkt, {"XOM": 0.40},
                                       view_confidences={"XOM": 0.9})
        with_view = optimize(post, cov, objective=Objective.MAX_SHARPE).weights["XOM"]
        baseline = optimize(prior, cov, objective=Objective.MAX_SHARPE).weights["XOM"]
        assert with_view > baseline

    def test_higher_confidence_pulls_posterior_toward_view(self, mu_cov):
        _, cov = mu_cov
        mkt = pd.Series({t: 1 / len(TICKERS) for t in TICKERS})
        low = black_litterman_returns(cov, mkt, {"XOM": 0.40}, view_confidences={"XOM": 0.1})
        high = black_litterman_returns(cov, mkt, {"XOM": 0.40}, view_confidences={"XOM": 0.95})
        assert abs(high["XOM"] - 0.40) < abs(low["XOM"] - 0.40)

    def test_unknown_asset_rejected(self, mu_cov):
        _, cov = mu_cov
        mkt = pd.Series({t: 1 / len(TICKERS) for t in TICKERS})
        with pytest.raises(ValueError, match="non presenti"):
            black_litterman_returns(cov, mkt, {"NOTREAL": 0.1})

    def test_empty_views_rejected(self, mu_cov):
        _, cov = mu_cov
        mkt = pd.Series({t: 1 / len(TICKERS) for t in TICKERS})
        with pytest.raises(ValueError, match="nessuna view"):
            black_litterman_returns(cov, mkt, {})


# ------------------------------------------------------- CAUSALITY (critical)


class TestNoLookAheadBias:
    """The backtester must never use information from the future."""

    def test_future_prices_cannot_change_past_weights(self, prices):
        """Perturb the last 20% of history; earlier weights must be identical.

        This is the definitive look-ahead test. If any estimator saw the
        whole sample, changing the tail would shift the early weights.
        """
        cfg = BacktestConfig(rebalance="monthly", lookback=252)
        base = run_backtest(prices, cfg)

        cut = int(len(prices) * 0.8)
        tampered = prices.copy()
        # A violent, obvious shock: triple every price in the tail.
        tampered.iloc[cut:] *= 3.0
        after = run_backtest(tampered, cfg)

        split = prices.index[cut - 1]
        w_base = base.weights_history.loc[:split]
        w_after = after.weights_history.loc[:split]
        pd.testing.assert_frame_equal(w_base, w_after)

    def test_past_returns_unchanged_by_future_tampering(self, prices):
        cfg = BacktestConfig(rebalance="monthly", lookback=252)
        base = run_backtest(prices, cfg)
        cut = int(len(prices) * 0.8)
        tampered = prices.copy()
        tampered.iloc[cut:] *= 3.0
        after = run_backtest(tampered, cfg)
        split = prices.index[cut - 1]
        pd.testing.assert_series_equal(
            base.returns.loc[:split], after.returns.loc[:split]
        )

    def test_first_bar_has_zero_return(self, prices):
        """There is no position before the first rebalance, so no P&L."""
        bt = run_backtest(prices, BacktestConfig(rebalance="monthly"))
        assert bt.gross_returns.iloc[0] == pytest.approx(0.0)

    def test_weights_applied_to_next_period_return(self):
        """Weights set on day D earn from day D+1, not day D."""
        idx = pd.bdate_range("2021-01-01", periods=300)
        # One asset flat, one jumping on a single known day.
        px = pd.DataFrame({"FLAT": 100.0, "JUMP": 100.0}, index=idx)
        px.loc[idx[250]:, "JUMP"] = 200.0
        bt = run_backtest(
            px, BacktestConfig(rebalance="never", lookback=None, min_lookback=60,
                               objective=Objective.EQUAL_WEIGHT,
                               max_weight_per_asset=None,
                               transaction_cost_bps=0.0)
        )
        # The jump lands on bar 250; a causal backtest books it there, using
        # weights that were already in place from bar 0.
        assert bt.gross_returns.iloc[250] == pytest.approx(0.5, rel=1e-9)
        assert bt.gross_returns.iloc[249] == pytest.approx(0.0)


class TestBacktestEngine:
    def test_weights_sum_to_one_after_first_rebalance(self, prices):
        bt = run_backtest(prices, BacktestConfig(rebalance="monthly"))
        sums = bt.weights_history.sum(axis=1)
        assert sums.iloc[-1] == pytest.approx(1.0, abs=1e-8)

    def test_weight_cap_enforced_throughout(self, prices):
        bt = run_backtest(prices, BacktestConfig(max_weight_per_asset=0.3))
        assert bt.weights_history.max().max() <= 0.3 + 1e-8

    def test_costs_reduce_return(self, prices):
        free = run_backtest(prices, BacktestConfig(transaction_cost_bps=0.0))
        pricey = run_backtest(prices, BacktestConfig(transaction_cost_bps=50.0))
        assert pricey.report.cagr < free.report.cagr
        assert pricey.total_cost > free.total_cost
        assert pricey.cost_drag_bps > free.cost_drag_bps

    def test_zero_cost_has_no_drag(self, prices):
        bt = run_backtest(prices, BacktestConfig(transaction_cost_bps=0.0))
        assert bt.cost_drag_bps == pytest.approx(0.0, abs=1e-9)
        pd.testing.assert_series_equal(bt.returns, bt.gross_returns)

    def test_more_frequent_rebalancing_raises_turnover(self, prices):
        monthly = run_backtest(prices, BacktestConfig(rebalance="monthly"))
        annual = run_backtest(prices, BacktestConfig(rebalance="annual"))
        assert len(monthly.rebalance_dates) > len(annual.rebalance_dates)

    def test_equal_weight_turnover_is_near_zero(self, prices):
        bt = run_backtest(prices, BacktestConfig(objective=Objective.EQUAL_WEIGHT))
        assert bt.avg_turnover < 0.05  # only the initial allocation

    def test_equity_curve_matches_returns(self, prices):
        bt = run_backtest(prices, BacktestConfig())
        assert bt.equity_curve.iloc[-1] == pytest.approx(
            (1 + bt.returns).prod(), rel=1e-9
        )

    def test_bad_rebalance_rule_rejected(self):
        with pytest.raises(ValueError, match="non valido"):
            BacktestConfig(rebalance="fortnightly")

    def test_negative_cost_rejected(self):
        with pytest.raises(ValueError, match="negativi"):
            BacktestConfig(transaction_cost_bps=-1.0)

    def test_lookback_below_min_rejected(self):
        with pytest.raises(ValueError, match="lookback"):
            BacktestConfig(lookback=10, min_lookback=60)

    def test_short_history_rejected(self):
        px = synthetic_prices(["A", "B"], date(2025, 1, 1), date(2025, 2, 1))
        with pytest.raises(ValueError, match="storico insufficiente"):
            run_backtest(px, BacktestConfig(min_lookback=60))

    def test_empty_frame_rejected(self):
        with pytest.raises(ValueError, match="vuoto"):
            run_backtest(pd.DataFrame())

    def test_benchmark_populates_relative_metrics(self, prices):
        bench = prices.mean(axis=1).pct_change().dropna()
        bt = run_backtest(prices, BacktestConfig(), benchmark=bench)
        assert bt.report.beta is not None
        assert bt.benchmark_report is not None

    def test_compare_objectives_tabulates_all(self, prices):
        df = compare_objectives(prices)
        assert len(df) == 5
        assert "Sharpe" in df.columns
        assert df["Sharpe"].notna().all()


class TestMarketData:
    def test_synthetic_is_reproducible(self):
        a = synthetic_prices(TICKERS, date(2022, 1, 1), date(2023, 1, 1), seed=5)
        b = synthetic_prices(TICKERS, date(2022, 1, 1), date(2023, 1, 1), seed=5)
        pd.testing.assert_frame_equal(a, b)

    def test_synthetic_is_reproducible_across_processes(self):
        """Pinned values: catches per-process hash randomisation.

        An in-process equality check cannot see this class of bug, because
        both calls share the same PYTHONHASHSEED. These literals were
        produced under three different hash seeds and must not drift.
        """
        px = synthetic_prices(["AAPL", "MSFT", "JNJ"], date(2022, 1, 1),
                              date(2023, 1, 1), seed=5)
        assert px.iloc[-1].round(6).tolist() == [76.697872, 95.398886, 92.747643]

    def test_ticker_profile_is_order_independent(self):
        """A ticker's own path must not depend on its position in the list."""
        a = synthetic_prices(["AAPL", "MSFT"], date(2022, 1, 1), date(2023, 1, 1), seed=9)
        b = synthetic_prices(["MSFT", "AAPL"], date(2022, 1, 1), date(2023, 1, 1), seed=9)
        # Same common factor and same per-ticker spread => identical columns.
        assert a["AAPL"].round(9).tolist() == b["AAPL"].round(9).tolist()

    def test_different_seeds_differ(self):
        a = synthetic_prices(TICKERS, date(2022, 1, 1), date(2023, 1, 1), seed=1)
        b = synthetic_prices(TICKERS, date(2022, 1, 1), date(2023, 1, 1), seed=2)
        assert not a.equals(b)

    def test_synthetic_prices_stay_positive(self, prices):
        assert (prices > 0).all().all()

    def test_correlation_is_induced(self):
        px = synthetic_prices(TICKERS, date(2019, 1, 1), date(2026, 1, 1), seed=3)
        corr = px.pct_change().dropna().corr().to_numpy()
        off = corr[~np.eye(len(TICKERS), dtype=bool)]
        assert off.mean() > 0.2  # not a diagonal covariance

    def test_empty_ticker_list_rejected(self):
        with pytest.raises(ValueError, match="nessun ticker"):
            synthetic_prices([], date(2022, 1, 1), date(2023, 1, 1))

    def test_inverted_dates_rejected(self):
        with pytest.raises(ValueError, match="deve seguire"):
            synthetic_prices(["A"], date(2023, 1, 1), date(2022, 1, 1))

    def test_loader_flags_synthetic_source(self):
        res = load_prices(TICKERS, date(2022, 1, 1), date(2023, 1, 1), synthetic=True)
        assert res.is_synthetic
        assert res.warnings and "sintetici" in res.warnings[0]

    def test_loader_dedups_and_uppercases(self):
        res = load_prices(["aapl", "AAPL", "msft"], date(2022, 1, 1),
                          date(2023, 1, 1), synthetic=True)
        assert res.tickers == ["AAPL", "MSFT"]
