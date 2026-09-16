"""Risk and performance metrics, implemented natively.

`ffn` and `empyrical` cover most of this, but both are heavy and pin old
pandas versions. Everything here is NumPy/pandas only, which keeps the
dependency surface small and the annualisation conventions explicit —
the usual source of disagreement between metric libraries.

Convention throughout: returns are *simple* (not log) periodic returns, and
`periods_per_year` states the annualisation factor (252 daily, 52 weekly,
12 monthly).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = ["PerformanceReport", "compute_metrics", "drawdown_series", "TRADING_DAYS"]

TRADING_DAYS = 252


@dataclass
class PerformanceReport:
    """Metric bundle for one return series."""

    total_return: float
    cagr: float
    volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    calmar: float
    var_95: float
    cvar_95: float
    ulcer_index: float
    win_rate: float
    best_period: float
    worst_period: float
    skew: float
    kurtosis: float
    periods: int
    alpha: float | None = None
    beta: float | None = None
    r_squared: float | None = None
    tracking_error: float | None = None
    information_ratio: float | None = None
    max_dd_start: pd.Timestamp | None = None
    max_dd_end: pd.Timestamp | None = None
    max_dd_recovery: pd.Timestamp | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self, *, pct: bool = True) -> dict[str, float | None]:
        """Flatten for display. `pct` scales ratio-free fields to percent."""
        s = 100.0 if pct else 1.0
        out: dict[str, float | None] = {
            "Total Return %": self.total_return * s,
            "CAGR %": self.cagr * s,
            "Volatility %": self.volatility * s,
            "Sharpe": self.sharpe,
            "Sortino": self.sortino,
            "Max Drawdown %": self.max_drawdown * s,
            "Calmar": self.calmar,
            "VaR 95% %": self.var_95 * s,
            "CVaR 95% %": self.cvar_95 * s,
            "Ulcer Index": self.ulcer_index,
            "Win Rate %": self.win_rate * s,
            "Skew": self.skew,
            "Kurtosis": self.kurtosis,
        }
        if self.beta is not None:
            out.update({
                "Alpha %": (self.alpha or 0.0) * s,
                "Beta": self.beta,
                "R²": self.r_squared,
                "Tracking Error %": (self.tracking_error or 0.0) * s,
                "Information Ratio": self.information_ratio,
            })
        return out


def _clean(returns: pd.Series) -> pd.Series:
    r = pd.Series(returns).dropna().astype(float)
    return r[np.isfinite(r)]


def drawdown_series(returns: pd.Series) -> pd.Series:
    """Drawdown from running peak, as a negative fraction."""
    r = _clean(returns)
    if r.empty:
        return pd.Series(dtype=float)
    equity = (1.0 + r).cumprod()
    return equity / equity.cummax() - 1.0


def _max_drawdown_window(
    returns: pd.Series,
) -> tuple[float, pd.Timestamp | None, pd.Timestamp | None, pd.Timestamp | None]:
    """Worst drawdown plus its peak, trough and recovery dates."""
    dd = drawdown_series(returns)
    if dd.empty:
        return 0.0, None, None, None

    trough = dd.idxmin()
    max_dd = float(dd.loc[trough])
    equity = (1.0 + _clean(returns)).cumprod()
    # Peak is the last index at or before the trough where drawdown was ~0.
    pre = dd.loc[:trough]
    peak_candidates = pre[pre >= -1e-12]
    peak = peak_candidates.index[-1] if len(peak_candidates) else pre.index[0]

    # Recovery is the first point after the trough regaining the old peak.
    post = equity.loc[trough:]
    peak_level = equity.loc[peak]
    regained = post[post >= peak_level]
    recovery = regained.index[0] if len(regained) else None
    return max_dd, peak, trough, recovery


def compute_metrics(
    returns: pd.Series,
    *,
    benchmark: pd.Series | None = None,
    risk_free_rate: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
) -> PerformanceReport:
    """Full metric set for `returns`.

    `risk_free_rate` is an *annual* rate and is de-annualised geometrically
    before being subtracted, so a 4% cash rate does not silently become 4%
    per period.
    """
    r = _clean(returns)
    notes: list[str] = []
    if r.empty:
        raise ValueError("serie di rendimenti vuota")
    if len(r) < 2:
        raise ValueError(f"servono almeno 2 osservazioni, trovate {len(r)}")
    if len(r) < periods_per_year // 4:
        notes.append(
            f"Solo {len(r)} osservazioni: metriche annualizzate poco affidabili."
        )

    n = len(r)
    years = n / periods_per_year
    equity = (1.0 + r).cumprod()
    total_return = float(equity.iloc[-1]) - 1.0

    # A wipeout makes CAGR undefined rather than -100%.
    if equity.iloc[-1] <= 0:
        cagr = -1.0
        notes.append("Capitale azzerato: CAGR troncato a -100%.")
    else:
        cagr = float(equity.iloc[-1]) ** (1.0 / years) - 1.0

    vol = float(r.std(ddof=1)) * np.sqrt(periods_per_year)
    rf_per_period = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = r - rf_per_period

    sharpe = (
        float(excess.mean()) / float(excess.std(ddof=1)) * np.sqrt(periods_per_year)
        if float(excess.std(ddof=1)) > 0
        else 0.0
    )

    # Sortino: downside deviation uses the full-length denominator, so a
    # strategy with few losses is not flattered by a tiny sample.
    downside = excess.clip(upper=0.0)
    dd_dev = float(np.sqrt((downside**2).sum() / n))
    sortino = (
        float(excess.mean()) / dd_dev * np.sqrt(periods_per_year)
        if dd_dev > 0
        else float("inf") if float(excess.mean()) > 0 else 0.0
    )

    max_dd, dd_start, dd_end, dd_rec = _max_drawdown_window(r)
    calmar = cagr / abs(max_dd) if max_dd < 0 else float("inf") if cagr > 0 else 0.0

    var_95 = float(np.percentile(r, 5))
    tail = r[r <= var_95]
    cvar_95 = float(tail.mean()) if len(tail) else var_95

    dd = drawdown_series(r)
    ulcer = float(np.sqrt((dd**2).mean())) if len(dd) else 0.0

    report = PerformanceReport(
        total_return=total_return,
        cagr=cagr,
        volatility=vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_dd,
        calmar=calmar,
        var_95=var_95,
        cvar_95=cvar_95,
        ulcer_index=ulcer,
        win_rate=float((r > 0).mean()),
        best_period=float(r.max()),
        worst_period=float(r.min()),
        skew=float(r.skew()),
        kurtosis=float(r.kurtosis()),
        periods=n,
        max_dd_start=dd_start,
        max_dd_end=dd_end,
        max_dd_recovery=dd_rec,
        notes=notes,
    )

    if benchmark is not None:
        _add_relative_metrics(report, r, benchmark, rf_per_period, periods_per_year)
    return report


def _add_relative_metrics(
    report: PerformanceReport,
    r: pd.Series,
    benchmark: pd.Series,
    rf_per_period: float,
    periods_per_year: int,
) -> None:
    """Attach alpha/beta/TE/IR, aligned on the shared index."""
    b = _clean(benchmark)
    joined = pd.concat([r, b], axis=1, join="inner").dropna()
    joined.columns = ["p", "b"]
    if len(joined) < 3:
        report.notes.append(
            f"Overlap con il benchmark troppo corto ({len(joined)} punti): "
            "alpha/beta non calcolati."
        )
        return
    if len(joined) < len(r):
        report.notes.append(
            f"Allineamento al benchmark: {len(r)} -> {len(joined)} osservazioni."
        )

    pe = joined["p"] - rf_per_period
    be = joined["b"] - rf_per_period
    var_b = float(be.var(ddof=1))
    if var_b <= 0:
        report.notes.append("Benchmark a varianza nulla: beta non definito.")
        return

    beta = float(be.cov(pe)) / var_b
    # Alpha annualised geometrically, consistent with CAGR.
    alpha_per_period = float(pe.mean()) - beta * float(be.mean())
    report.beta = beta
    report.alpha = (1.0 + alpha_per_period) ** periods_per_year - 1.0
    corr = float(np.corrcoef(joined["p"], joined["b"])[0, 1])
    report.r_squared = corr**2

    active = joined["p"] - joined["b"]
    te = float(active.std(ddof=1)) * np.sqrt(periods_per_year)
    report.tracking_error = te
    report.information_ratio = (
        float(active.mean()) * periods_per_year / te if te > 0 else 0.0
    )
