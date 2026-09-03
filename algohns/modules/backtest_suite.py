"""Module 3 — Advanced Backtesting & Portfolio Optimization Suite.

Glue between three quant pillars:

* **PyPortfolioOpt** — mean-variance / Black-Litterman / risk-parity optimizers.
* **ffn / vectorbt** — battle-tested performance analytics (optional).
* A self-contained pure-pandas metrics engine so Sharpe, Sortino, Calmar,
  drawdown, alpha and beta are always available even without the extras.

The public surface is intentionally small:

    optimizer = PortfolioOptimizer(prices)
    weights   = optimizer.optimize("max_sharpe")
    report    = Backtester(prices).run(weights, benchmark="SPY")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from ..core.utils import is_available, lazy_import, require, safe_ratio

_pfopt = lazy_import("pypfopt", pip_name="PyPortfolioOpt", reason="optimize portfolios")
_ffn = lazy_import("ffn", pip_name="ffn", reason="compute extended performance stats")

TRADING_DAYS = 252

OptMethod = Literal[
    "max_sharpe", "min_volatility", "risk_parity", "equal_weight", "black_litterman"
]


# ---------------------------------------------------------------------------
# Metrics engine (always available)
# ---------------------------------------------------------------------------
@dataclass
class PerformanceMetrics:
    total_return: float
    cagr: float
    annual_volatility: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown: float
    alpha: float
    beta: float
    var_95: float
    cvar_95: float

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 6) for k, v in self.__dict__.items()}


def compute_metrics(
    returns: pd.Series,
    benchmark_returns: pd.Series | None = None,
    risk_free: float = 0.0,
) -> PerformanceMetrics:
    """Compute a full metrics suite from a daily-returns series."""
    returns = returns.dropna()
    if returns.empty:
        raise ValueError("empty returns series")

    rf_daily = risk_free / TRADING_DAYS
    excess = returns - rf_daily
    equity = (1 + returns).cumprod()

    total_return = float(equity.iloc[-1] - 1)
    years = max(len(returns) / TRADING_DAYS, 1e-9)
    cagr = float(equity.iloc[-1] ** (1 / years) - 1)
    ann_vol = float(returns.std(ddof=0) * np.sqrt(TRADING_DAYS))

    sharpe = safe_ratio(excess.mean() * TRADING_DAYS, returns.std(ddof=0) * np.sqrt(TRADING_DAYS))
    downside = returns[returns < 0].std(ddof=0)
    sortino = safe_ratio(excess.mean() * TRADING_DAYS, downside * np.sqrt(TRADING_DAYS))

    # Drawdown
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    max_dd = float(drawdown.min())
    calmar = safe_ratio(cagr, abs(max_dd))

    # Alpha / Beta vs benchmark
    alpha = beta = 0.0
    if benchmark_returns is not None:
        aligned = pd.concat([returns, benchmark_returns], axis=1, join="inner").dropna()
        if len(aligned) > 2:
            y = aligned.iloc[:, 0] - rf_daily
            x = aligned.iloc[:, 1] - rf_daily
            var_x = x.var(ddof=0)
            beta = safe_ratio(np.cov(y, x, ddof=0)[0, 1], var_x)
            alpha = float((y.mean() - beta * x.mean()) * TRADING_DAYS)

    var_95 = float(np.percentile(returns, 5))
    cvar_95 = float(returns[returns <= var_95].mean()) if (returns <= var_95).any() else var_95

    return PerformanceMetrics(
        total_return=total_return,
        cagr=cagr,
        annual_volatility=ann_vol,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        max_drawdown=max_dd,
        alpha=alpha,
        beta=beta,
        var_95=var_95,
        cvar_95=cvar_95,
    )


# ---------------------------------------------------------------------------
# Portfolio optimization
# ---------------------------------------------------------------------------
class PortfolioOptimizer:
    """Wraps PyPortfolioOpt with graceful pure-numpy fallbacks."""

    def __init__(self, prices: pd.DataFrame) -> None:
        self.prices = prices.dropna(how="all").ffill().dropna()
        if self.prices.shape[1] < 2:
            raise ValueError("need at least 2 assets to optimize a portfolio")
        self.returns = self.prices.pct_change().dropna()

    # ------------------------------------------------------------- dispatch
    def optimize(self, method: OptMethod = "max_sharpe", **kwargs) -> dict[str, float]:
        method = method.lower()
        if method == "equal_weight":
            return self._equal_weight()
        if is_available(_pfopt):
            return self._optimize_pfopt(method, **kwargs)
        return self._optimize_fallback(method)

    # ------------------------------------------------------------ pfopt path
    def _optimize_pfopt(self, method: str, **kwargs) -> dict[str, float]:
        from pypfopt import EfficientFrontier, expected_returns, risk_models
        from pypfopt import objective_functions

        mu = expected_returns.mean_historical_return(self.prices)
        S = risk_models.CovarianceShrinkage(self.prices).ledoit_wolf()

        if method == "max_sharpe":
            ef = EfficientFrontier(mu, S)
            ef.add_objective(objective_functions.L2_reg, gamma=0.1)
            ef.max_sharpe()
            return self._clean(ef.clean_weights())
        if method == "min_volatility":
            ef = EfficientFrontier(mu, S)
            ef.min_volatility()
            return self._clean(ef.clean_weights())
        if method == "risk_parity":
            return self._risk_parity(S)
        if method == "black_litterman":
            return self._black_litterman(S, **kwargs)
        raise ValueError(f"unknown optimization method: {method}")

    def _black_litterman(self, S, views: dict[str, float] | None = None, **_):
        from pypfopt import BlackLittermanModel, expected_returns
        from pypfopt import EfficientFrontier

        market_prior = expected_returns.mean_historical_return(self.prices)
        views = views or {c: float(market_prior[c]) for c in self.prices.columns}
        bl = BlackLittermanModel(S, pi=market_prior, absolute_views=views)
        ret_bl = bl.bl_returns()
        cov_bl = bl.bl_cov()
        ef = EfficientFrontier(ret_bl, cov_bl)
        ef.max_sharpe()
        return self._clean(ef.clean_weights())

    def _risk_parity(self, S) -> dict[str, float]:
        """Simple inverse-volatility risk parity."""
        cov = np.asarray(S)
        vol = np.sqrt(np.diag(cov))
        inv = 1.0 / vol
        w = inv / inv.sum()
        return self._clean(dict(zip(self.prices.columns, w)))

    # --------------------------------------------------------- numpy fallback
    def _optimize_fallback(self, method: str) -> dict[str, float]:
        cov = self.returns.cov().values * TRADING_DAYS
        mu = self.returns.mean().values * TRADING_DAYS
        n = len(mu)
        if method in ("min_volatility", "risk_parity"):
            vol = np.sqrt(np.diag(cov))
            inv = 1.0 / vol
            w = inv / inv.sum()
        elif method == "max_sharpe":
            # Unconstrained tangency portfolio, projected to the simplex.
            try:
                inv_cov = np.linalg.pinv(cov)
                w = inv_cov @ mu
                w = np.clip(w, 0, None)
                w = w / w.sum() if w.sum() else np.ones(n) / n
            except Exception:  # noqa: BLE001
                w = np.ones(n) / n
        else:  # black_litterman fallback == equal weight prior
            w = np.ones(n) / n
        return self._clean(dict(zip(self.prices.columns, w)))

    def _equal_weight(self) -> dict[str, float]:
        n = self.prices.shape[1]
        return {c: 1.0 / n for c in self.prices.columns}

    @staticmethod
    def _clean(weights: dict) -> dict[str, float]:
        cleaned = {k: float(v) for k, v in weights.items() if abs(float(v)) > 1e-6}
        total = sum(cleaned.values()) or 1.0
        normed = {k: round(v / total, 6) for k, v in cleaned.items()}
        # Absorb the rounding residual into the largest weight so the
        # allocation sums to exactly 1.0 (avoids drift in rebalancing).
        residual = round(1.0 - sum(normed.values()), 6)
        if normed and residual:
            kmax = max(normed, key=normed.get)
            normed[kmax] = round(normed[kmax] + residual, 6)
        return normed

    # --------------------------------------------------------------- report
    def expected_performance(self, weights: dict[str, float]) -> dict[str, float]:
        w = np.array([weights.get(c, 0.0) for c in self.prices.columns])
        mu = self.returns.mean().values * TRADING_DAYS
        cov = self.returns.cov().values * TRADING_DAYS
        exp_ret = float(w @ mu)
        exp_vol = float(np.sqrt(w @ cov @ w))
        return {
            "expected_return": round(exp_ret, 6),
            "expected_volatility": round(exp_vol, 6),
            "expected_sharpe": round(safe_ratio(exp_ret, exp_vol), 4),
        }


# ---------------------------------------------------------------------------
# Backtester
# ---------------------------------------------------------------------------
@dataclass
class BacktestResult:
    weights: dict[str, float]
    metrics: PerformanceMetrics
    equity_curve: pd.Series
    drawdown_curve: pd.Series
    benchmark_curve: pd.Series | None = field(default=None)

    def summary(self) -> dict:
        out = self.metrics.as_dict()
        out["final_equity"] = round(float(self.equity_curve.iloc[-1]), 4)
        return out


class Backtester:
    """Buy-and-hold / periodically-rebalanced portfolio backtester."""

    def __init__(self, prices: pd.DataFrame, initial_capital: float = 100_000.0) -> None:
        self.prices = prices.dropna(how="all").ffill().dropna()
        self.initial_capital = initial_capital

    def run(
        self,
        weights: dict[str, float],
        rebalance: Literal["none", "M", "Q", "Y"] = "Q",
        benchmark: pd.Series | None = None,
        risk_free: float = 0.0,
    ) -> BacktestResult:
        cols = [c for c in weights if c in self.prices.columns]
        px = self.prices[cols]
        rets = px.pct_change().dropna()
        w = pd.Series({c: weights[c] for c in cols})
        w = w / w.sum()

        if rebalance == "none":
            port_ret = rets.dot(w)
        else:
            port_ret = self._rebalanced_returns(rets, w, rebalance)

        equity = self.initial_capital * (1 + port_ret).cumprod()
        running_max = equity.cummax()
        drawdown = equity / running_max - 1

        bench_ret = None
        bench_curve = None
        if benchmark is not None:
            bench_ret = benchmark.reindex(port_ret.index).pct_change().dropna() \
                if benchmark.max() > 5 else benchmark.reindex(port_ret.index).dropna()
            if not bench_ret.empty:
                bench_curve = self.initial_capital * (1 + bench_ret).cumprod()

        metrics = compute_metrics(port_ret, bench_ret, risk_free=risk_free)
        return BacktestResult(
            weights=weights,
            metrics=metrics,
            equity_curve=equity,
            drawdown_curve=drawdown,
            benchmark_curve=bench_curve,
        )

    @staticmethod
    def _rebalanced_returns(rets: pd.DataFrame, target: pd.Series, freq: str) -> pd.Series:
        """Return portfolio returns with weights reset to target each period."""
        out = []
        # Group by calendar period; within each period weights drift.
        grouper = rets.groupby(rets.index.to_period(freq))
        for _, block in grouper:
            drift = (1 + block).cumprod()
            weighted = (drift * target).sum(axis=1)
            port_val = weighted / weighted.shift(1)
            port_val.iloc[0] = float((block.iloc[0] * target).sum() + 1)
            out.append(port_val - 1)
        series = pd.concat(out).sort_index()
        return series.reindex(rets.index).fillna(0.0)

    # ----------------------------------------------------- ffn cross-check
    def ffn_stats(self, equity: pd.Series) -> dict | None:
        """Extended stats from the `ffn` library, if installed."""
        if not is_available(_ffn):
            return None
        try:
            stats = _ffn.core.PerformanceStats(equity)
            return {
                "cagr": round(float(stats.cagr), 6),
                "sharpe": round(float(stats.daily_sharpe), 4),
                "sortino": round(float(stats.daily_sortino), 4),
                "max_drawdown": round(float(stats.max_drawdown), 6),
                "calmar": round(float(stats.calmar), 4),
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
