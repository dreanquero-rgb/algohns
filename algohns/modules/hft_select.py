"""Algorithmic stock selection for the HFT section — pick the universe, don't type it.

Instead of entering tickers by hand, an HFT strategy screens a base universe
(S&P 500 — bundled offline and US-tradable) by **sector** and ranks it by a
**short-term statistic** (momentum, mean-reversion, volatility), then trades the
top N names. The intraday engine (hft_strategies) then decides *when* to trade
them; this module decides *which*.

Fundamentals (EPS/PE/ratios) are not available for the whole market, so the
variables here are the ones computable from prices + the bundled sector map:
sector, return over a lookback, and realized volatility.
"""
from __future__ import annotations

import pandas as pd

from .reference_data import sp500_constituents

__all__ = ["METRICS", "sp500_sectors", "candidate_symbols", "select", "rank"]

# Ranking variables available for picking the universe.
METRICS = {
    "momentum": "Momentum — return più alto nel periodo (titoli forti)",
    "mean_reversion": "Mean reversion — return più basso (ipervenduti)",
    "high_volatility": "Alta volatilità — più movimento intraday",
    "low_volatility": "Bassa volatilità — più stabili",
}


def sp500_sectors() -> list[str]:
    """The GICS sectors present in the bundled S&P 500 list."""
    df = sp500_constituents()
    if df.empty or "GICS Sector" not in df.columns:
        return []
    return sorted(df["GICS Sector"].dropna().unique().tolist())


def candidate_symbols(sectors: list[str] | None = None) -> list[str]:
    """S&P 500 tickers, optionally restricted to the given sectors."""
    df = sp500_constituents()
    if df.empty or "Symbol" not in df.columns:
        return []
    if sectors:
        df = df[df["GICS Sector"].isin(sectors)]
    return df["Symbol"].astype(str).str.upper().str.strip().tolist()


def rank(prices: pd.DataFrame, metric: str, lookback: int, n: int) -> list[str]:
    """Rank a daily-close price frame (columns = tickers) and return the top N.

    Pure function — testable without the network.
    """
    if prices is None or prices.empty:
        return []
    px = prices.copy()
    if isinstance(px, pd.Series):
        px = px.to_frame()
    px = px.apply(pd.to_numeric, errors="coerce").dropna(how="all", axis=1)
    if px.empty:
        return []
    tail = px.tail(max(int(lookback) + 1, 2))
    if metric in ("momentum", "mean_reversion"):
        score = tail.iloc[-1] / tail.iloc[0] - 1.0            # return over window
        ascending = metric == "mean_reversion"
    else:
        score = tail.pct_change().std()                       # realized volatility
        ascending = metric == "low_volatility"
    ranked = score.replace([float("inf"), float("-inf")], pd.NA).dropna()
    ranked = ranked.sort_values(ascending=ascending)
    return [str(t) for t in ranked.head(int(n)).index]


def select(sectors: list[str] | None, metric: str, lookback_days: int = 21,
           n: int = 10, period: str = "3mo") -> list[str]:
    """Resolve the HFT universe: filter by sector, fetch prices, rank, pick N."""
    syms = candidate_symbols(sectors)
    if not syms:
        return []
    # Lazy import so this module stays importable without the data stack.
    from ..core.data_providers import get_market_data
    prices = get_market_data().history(syms, period=period, interval="1d")
    return rank(prices, metric, lookback_days, n)
