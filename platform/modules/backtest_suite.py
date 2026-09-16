"""MODULE 3 — Advanced Backtesting & Portfolio Optimization Suite.

Walk-forward backtesting with periodic re-optimisation, transaction costs
and strictly causal weights.

The one thing this module refuses to get wrong is **look-ahead bias**. At
each rebalance date the optimiser sees only `prices.loc[:date]`, and the
weights it produces are applied to the *following* period's returns. Fitting
on the whole sample and then "backtesting" on it is the single most common
way a strategy looks brilliant on a laptop and loses money in production.

A `lookback` window is also enforced: without it, early rebalances estimate
covariance from a handful of observations and the resulting weights are
noise, which flatters the equity curve exactly where it is least reliable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from modules.metrics import TRADING_DAYS, PerformanceReport, compute_metrics
from modules.optimizers import (
    Objective,
    OptimizationResult,
    covariance,
    expected_returns,
    optimize,
)

__all__ = ["BacktestConfig", "BacktestResult", "run_backtest", "compare_objectives"]

log = logging.getLogger(__name__)

REBALANCE_RULES = {
    "daily": "B",
    "weekly": "W-FRI",
    "monthly": "BME",
    "quarterly": "BQE",
    "annual": "BYE",
    "never": None,
}


@dataclass
class BacktestConfig:
    objective: Objective = Objective.MAX_SHARPE
    rebalance: str = "monthly"
    # Trailing window for mu/cov estimation, in periods. None uses all data
    # up to the rebalance date (expanding window).
    lookback: int | None = 504
    min_lookback: int = 60
    transaction_cost_bps: float = 5.0
    risk_free_rate: float = 0.02
    max_weight_per_asset: float | None = 0.35
    shrinkage: bool = True
    return_method: str = "mean"
    periods_per_year: int = TRADING_DAYS

    def __post_init__(self) -> None:
        if self.rebalance not in REBALANCE_RULES:
            raise ValueError(
                f"rebalance {self.rebalance!r} non valido; "
                f"scegli tra {sorted(REBALANCE_RULES)}"
            )
        if self.lookback is not None and self.lookback < self.min_lookback:
            raise ValueError(
                f"lookback ({self.lookback}) < min_lookback ({self.min_lookback})"
            )
        if self.transaction_cost_bps < 0:
            raise ValueError("i costi di transazione non possono essere negativi")


@dataclass
class BacktestResult:
    config: BacktestConfig
    returns: pd.Series                      # net of costs
    gross_returns: pd.Series
    equity_curve: pd.Series
    weights_history: pd.DataFrame
    report: PerformanceReport
    benchmark_report: PerformanceReport | None = None
    rebalance_dates: list[pd.Timestamp] = field(default_factory=list)
    total_cost: float = 0.0
    turnover_history: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    optimizer_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def cost_drag_bps(self) -> float:
        """Annualised CAGR give-up from trading costs, in basis points."""
        gross = compute_metrics(
            self.gross_returns,
            risk_free_rate=self.config.risk_free_rate,
            periods_per_year=self.config.periods_per_year,
        )
        return (gross.cagr - self.report.cagr) * 10_000

    @property
    def avg_turnover(self) -> float:
        return float(self.turnover_history.mean()) if len(self.turnover_history) else 0.0

    def final_weights(self) -> pd.Series:
        if self.weights_history.empty:
            return pd.Series(dtype=float)
        return self.weights_history.iloc[-1].sort_values(ascending=False)


def _rebalance_dates(
    index: pd.DatetimeIndex, rule: str, min_lookback: int
) -> list[pd.Timestamp]:
    """Dates on which to re-optimise.

    Dates earlier than `min_lookback` bars in are dropped: there is not
    enough history to estimate anything there, and sizing a portfolio off
    ten observations produces noise weights that flatter the early equity
    curve. The portfolio therefore stays in cash until the first feasible
    date, which is always included.
    """
    if len(index) < min_lookback:
        return []
    first_feasible = index[min_lookback - 1]

    freq = REBALANCE_RULES[rule]
    if freq is None:
        # Allocate once, at the earliest date with enough history, then hold.
        return [first_feasible]

    marks = pd.Series(1, index=index).resample(freq).last().dropna().index
    # Snap each period end back onto an actual trading day in the index.
    dates: list[pd.Timestamp] = []
    for m in marks:
        eligible = index[index <= m]
        if len(eligible):
            dates.append(eligible[-1])
    dates = [d for d in dates if d >= first_feasible]
    if first_feasible not in dates:
        dates.insert(0, first_feasible)
    return sorted(set(dates))


def run_backtest(
    prices: pd.DataFrame,
    config: BacktestConfig | None = None,
    *,
    benchmark: pd.Series | None = None,
    market_weights: pd.Series | None = None,
    views: dict[str, float] | None = None,
) -> BacktestResult:
    """Walk-forward backtest of `config.objective` over `prices`.

    `views` activates Black-Litterman at each rebalance, blending the
    equilibrium prior implied by `market_weights` with the supplied views.
    """
    config = config or BacktestConfig()
    prices = prices.sort_index().dropna(how="all")
    if prices.empty:
        raise ValueError("DataFrame prezzi vuoto")
    if len(prices) < config.min_lookback + 2:
        raise ValueError(
            f"storico insufficiente: {len(prices)} barre, "
            f"servono almeno {config.min_lookback + 2}"
        )

    warnings: list[str] = []
    # Drop columns that are entirely missing; keep partial ones (handled below).
    dead = [c for c in prices.columns if prices[c].notna().sum() < 2]
    if dead:
        warnings.append(f"Colonne senza dati rimosse: {', '.join(dead)}")
        prices = prices.drop(columns=dead)
    if prices.shape[1] == 0:
        raise ValueError("nessun asset con dati utilizzabili")

    asset_returns = prices.pct_change().fillna(0.0)
    rb_dates = _rebalance_dates(prices.index, config.rebalance, config.min_lookback)

    weights = pd.Series(0.0, index=prices.columns)
    rows: dict[pd.Timestamp, pd.Series] = {}
    gross: dict[pd.Timestamp, float] = {}
    net: dict[pd.Timestamp, float] = {}
    turnover: dict[pd.Timestamp, float] = {}
    opt_notes: list[str] = []
    total_cost = 0.0
    cost_rate = config.transaction_cost_bps / 10_000.0
    rebalanced_on: list[pd.Timestamp] = []

    for i, ts in enumerate(prices.index):
        # --- Re-optimise using ONLY data strictly up to and including today.
        if ts in rb_dates:
            window = prices.loc[:ts]
            if config.lookback is not None:
                window = window.tail(config.lookback)
            if len(window) >= config.min_lookback:
                new_w = _solve(window, config, market_weights, views, opt_notes)
                if new_w is not None:
                    trade = float((new_w - weights).abs().sum())
                    turnover[ts] = trade
                    cost = trade * cost_rate
                    total_cost += cost
                    weights = new_w
                    rebalanced_on.append(ts)
                    # Charge the cost on the rebalance bar itself.
                    net[ts] = net.get(ts, 0.0) - cost

        rows[ts] = weights.copy()

        # --- Apply yesterday's weights to today's return (causal).
        if i == 0:
            gross[ts] = 0.0
            net[ts] = net.get(ts, 0.0) + 0.0
            continue
        period_ret = float((weights * asset_returns.loc[ts]).sum())
        gross[ts] = period_ret
        net[ts] = net.get(ts, 0.0) + period_ret

    gross_s = pd.Series(gross).sort_index()
    net_s = pd.Series(net).sort_index().reindex(gross_s.index).fillna(0.0)
    equity = (1.0 + net_s).cumprod()

    if not rebalanced_on:
        # The optimiser notes say *why* every attempt failed (infeasible
        # weight cap, singular covariance, too few usable assets). Without
        # them this error sends you hunting the wrong problem.
        detail = "; ".join(sorted(set(opt_notes))) or (
            f"lookback troppo lungo per lo storico disponibile "
            f"({len(prices)} barre)"
        )
        raise ValueError(f"nessun ribilanciamento eseguito: {detail}")

    report = compute_metrics(
        net_s, benchmark=benchmark, risk_free_rate=config.risk_free_rate,
        periods_per_year=config.periods_per_year,
    )
    bench_report = None
    if benchmark is not None:
        try:
            bench_report = compute_metrics(
                benchmark, risk_free_rate=config.risk_free_rate,
                periods_per_year=config.periods_per_year,
            )
        except ValueError as exc:
            warnings.append(f"Benchmark non valutabile: {exc}")

    return BacktestResult(
        config=config,
        returns=net_s,
        gross_returns=gross_s,
        equity_curve=equity,
        weights_history=pd.DataFrame(rows).T,
        report=report,
        benchmark_report=bench_report,
        rebalance_dates=rebalanced_on,
        total_cost=total_cost,
        turnover_history=pd.Series(turnover).sort_index(),
        optimizer_notes=sorted(set(opt_notes)),
        warnings=warnings,
    )


def _solve(
    window: pd.DataFrame,
    config: BacktestConfig,
    market_weights: pd.Series | None,
    views: dict[str, float] | None,
    notes: list[str],
) -> pd.Series | None:
    """Optimise on `window`; return None when the window is unusable."""
    # Only assets with a full history in this window can be sized reliably.
    usable = [c for c in window.columns if window[c].notna().sum() >= config.min_lookback]
    if len(usable) < 2:
        notes.append(
            f"Finestra al {window.index[-1].date()}: <2 asset con storico "
            "sufficiente, pesi invariati."
        )
        return None
    w = window[usable].ffill().dropna(how="any")
    if len(w) < config.min_lookback:
        return None

    try:
        cov = covariance(w, shrinkage=config.shrinkage,
                         periods_per_year=config.periods_per_year)
        mu = expected_returns(w, method=config.return_method,
                              periods_per_year=config.periods_per_year)
        if views:
            from modules.optimizers import black_litterman_returns

            mkt = (
                market_weights.reindex(usable).fillna(0.0)
                if market_weights is not None
                else pd.Series(1.0 / len(usable), index=usable)
            )
            active = {k: v for k, v in views.items() if k in usable}
            if active:
                mu = black_litterman_returns(
                    cov, mkt, active, risk_free_rate=config.risk_free_rate
                )
        result: OptimizationResult = optimize(
            mu, cov,
            objective=config.objective,
            risk_free_rate=config.risk_free_rate,
            max_weight_per_asset=config.max_weight_per_asset,
        )
    except (ValueError, np.linalg.LinAlgError) as exc:
        notes.append(f"Ottimizzazione al {window.index[-1].date()} fallita: {exc}")
        return None

    notes.extend(result.notes)
    # Re-expand onto the full asset list; excluded names get zero.
    return pd.Series(result.weights).reindex(window.columns).fillna(0.0)


def compare_objectives(
    prices: pd.DataFrame,
    objectives: list[Objective] | None = None,
    config: BacktestConfig | None = None,
    *,
    benchmark: pd.Series | None = None,
) -> pd.DataFrame:
    """Backtest several objectives on identical data and tabulate the result.

    Same price history, same rebalance dates, same costs — so the comparison
    isolates the objective rather than the setup.
    """
    objectives = objectives or [
        Objective.MAX_SHARPE, Objective.MIN_VARIANCE,
        Objective.RISK_PARITY, Objective.HRP, Objective.EQUAL_WEIGHT,
    ]
    base = config or BacktestConfig()
    rows = []
    for obj in objectives:
        cfg = BacktestConfig(**{**base.__dict__, "objective": obj})
        try:
            res = run_backtest(prices, cfg, benchmark=benchmark)
        except ValueError as exc:
            rows.append({"Objective": obj.value, "Error": str(exc)})
            continue
        m = res.report.to_dict()
        rows.append({
            "Objective": obj.value,
            "CAGR %": m["CAGR %"],
            "Vol %": m["Volatility %"],
            "Sharpe": m["Sharpe"],
            "Sortino": m["Sortino"],
            "MaxDD %": m["Max Drawdown %"],
            "Calmar": m["Calmar"],
            "Avg Turnover": res.avg_turnover,
            "Cost drag bps": res.cost_drag_bps,
        })
    return pd.DataFrame(rows).set_index("Objective")
