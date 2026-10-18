"""Built-in intraday / high-frequency strategies (statistical).

Each strategy maps a price history (1-minute bars) to a **position signal** in
{-1, 0, +1}: +1 long, -1 short/exit, 0 flat. Pure NumPy/pandas — fast,
testable offline, and safe to edit from Code Studio.

A strategy's tunables live in its ``params`` schema ({name: default}); the HFT
page renders inputs from that schema and feeds the chosen values back in via
:func:`compute_signals`. Add your own strategy by editing this file in Code
Studio: define ``f(prices, **params) -> pd.Series`` and register it in
``HFT_STRATEGIES``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "HFT_STRATEGIES",
    "momentum",
    "mean_reversion",
    "breakout",
    "compute_signals",
    "latest_signal",
    "quick_backtest",
]


def _series(prices) -> pd.Series:
    """Accept a close Series or an OHLC(V) DataFrame; return the close series."""
    if isinstance(prices, pd.DataFrame):
        for col in ("Close", "close", "Adj Close"):
            if col in prices.columns:
                return pd.to_numeric(prices[col], errors="coerce")
        return pd.to_numeric(prices.iloc[:, 0], errors="coerce")
    return pd.to_numeric(pd.Series(prices), errors="coerce")


def momentum(prices, lookback: int = 20, threshold: float = 0.001) -> pd.Series:
    """Go long when the ``lookback``-bar return clears +threshold, short below."""
    close = _series(prices)
    ret = close.pct_change(int(lookback))
    sig = pd.Series(0, index=close.index, dtype=int)
    sig[ret > threshold] = 1
    sig[ret < -threshold] = -1
    return sig.fillna(0).astype(int)


def mean_reversion(prices, lookback: int = 20, z: float = 1.5) -> pd.Series:
    """Fade extremes: long when z-score < -z (oversold), short when > +z."""
    close = _series(prices)
    ma = close.rolling(int(lookback)).mean()
    sd = close.rolling(int(lookback)).std().replace(0, np.nan)
    zscore = (close - ma) / sd
    sig = pd.Series(0, index=close.index, dtype=int)
    sig[zscore < -z] = 1
    sig[zscore > z] = -1
    return sig.fillna(0).astype(int)


def breakout(prices, lookback: int = 20) -> pd.Series:
    """Donchian breakout: long at a new ``lookback`` high, short at a new low."""
    close = _series(prices)
    hi = close.rolling(int(lookback)).max()
    lo = close.rolling(int(lookback)).min()
    sig = pd.Series(0, index=close.index, dtype=int)
    sig[close >= hi] = 1
    sig[close <= lo] = -1
    return sig.fillna(0).astype(int)


# Registry: key -> {label, fn, params schema (name: default)}.
HFT_STRATEGIES: dict[str, dict] = {
    "momentum": {
        "label": "Momentum",
        "fn": momentum,
        "params": {"lookback": 20, "threshold": 0.001},
    },
    "mean_reversion": {
        "label": "Mean reversion (z-score)",
        "fn": mean_reversion,
        "params": {"lookback": 20, "z": 1.5},
    },
    "breakout": {
        "label": "Breakout (Donchian)",
        "fn": breakout,
        "params": {"lookback": 20},
    },
}


def compute_signals(kind: str, prices, params: dict | None = None) -> pd.Series:
    """Run strategy ``kind`` over ``prices`` with ``params`` (defaults filled)."""
    if kind not in HFT_STRATEGIES:
        raise KeyError(f"unknown strategy {kind!r}")
    entry = HFT_STRATEGIES[kind]
    merged = dict(entry["params"])
    merged.update(params or {})
    return entry["fn"](prices, **merged)


def latest_signal(kind: str, prices, params: dict | None = None) -> int:
    """The signal on the most recent bar (-1/0/+1), or 0 if undefined."""
    sig = compute_signals(kind, prices, params)
    if sig.empty:
        return 0
    return int(sig.iloc[-1])


def quick_backtest(prices, signal: pd.Series) -> dict:
    """Toy vectorised backtest: next-bar return * position. Informative, not a
    promise — no costs, no slippage, no fills. Returns summary stats + the
    cumulative equity series for a quick sanity chart."""
    close = _series(prices).reindex(signal.index)
    fwd = close.pct_change().shift(-1)            # return realised next bar
    pnl = (signal * fwd).fillna(0.0)
    equity = (1.0 + pnl).cumprod()
    trades = int((signal.diff().fillna(signal) != 0).sum())
    total = float(equity.iloc[-1] - 1.0) if len(equity) else 0.0
    vol = float(pnl.std())
    sharpe = float(pnl.mean() / vol * np.sqrt(252 * 390)) if vol else 0.0  # 1-min bars
    return {
        "total_return": total,
        "sharpe_annualised": sharpe,
        "trades": trades,
        "equity": equity,
    }
