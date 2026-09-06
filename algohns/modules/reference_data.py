"""Bundled real reference datasets (work fully offline).

Shipped with the repo so the platform always has genuine data to show, even
where live market APIs are unreachable:

* ``sp500_constituents.csv`` — the real S&P 500 index members with GICS sector,
  sub-industry, headquarters, date added and **SEC CIK**.
* ``spx_monthly_1871.csv``  — S&P 500 index monthly since 1871 with dividends,
  earnings, CPI, long interest rate and PE10.
* ``us10y_monthly.csv``     — US 10-year Treasury yield, monthly since 1953.

Source: the public `datasets/` open-data collection on GitHub.
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

from ..config import get_settings

_DATA = get_settings().data_dir


@lru_cache(maxsize=1)
def sp500_constituents() -> pd.DataFrame:
    """Real S&P 500 members: Symbol, Security, GICS Sector/Sub-Industry, CIK."""
    path = _DATA / "sp500_constituents.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    if "CIK" in df.columns:
        df["CIK"] = df["CIK"].astype("Int64")
    return df


def sp500_tickers(limit: int | None = None) -> list[str]:
    df = sp500_constituents()
    if df.empty:
        return []
    tickers = df["Symbol"].astype(str).tolist()
    return tickers[:limit] if limit else tickers


def sector_breakdown() -> pd.Series:
    """Company count per GICS sector (real index composition)."""
    df = sp500_constituents()
    if df.empty or "GICS Sector" not in df:
        return pd.Series(dtype=int)
    return df["GICS Sector"].value_counts()


@lru_cache(maxsize=1)
def cik_map() -> dict[str, str]:
    """ticker -> zero-padded CIK, from the bundled constituents (offline)."""
    df = sp500_constituents()
    if df.empty or "CIK" not in df:
        return {}
    return {str(r.Symbol).upper(): str(int(r.CIK)).zfill(10)
            for r in df.itertuples() if pd.notna(r.CIK)}


@lru_cache(maxsize=1)
def spx_history() -> pd.DataFrame:
    """S&P 500 monthly since 1871 (price, dividend, earnings, CPI, rates, PE10)."""
    path = _DATA / "spx_monthly_1871.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["Date"]).set_index("Date").sort_index()
    df = df.apply(pd.to_numeric, errors="coerce")
    # Rows past the last real observation carry zeros in the derived columns.
    return df[df["SP500"] > 0]


@lru_cache(maxsize=1)
def us10y() -> pd.Series:
    """US 10-year Treasury yield, monthly since 1953 (percent)."""
    path = _DATA / "us10y_monthly.csv"
    if not path.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=["Date"]).set_index("Date").sort_index()
    return pd.to_numeric(df["Rate"], errors="coerce").dropna().rename("US 10Y yield %")


def spx_prices(start: str = "1990-01-01") -> pd.Series:
    """S&P 500 index level from `start` — real data for long backtests."""
    hist = spx_history()
    if hist.empty:
        return pd.Series(dtype=float)
    return hist.loc[hist.index >= start, "SP500"].rename("S&P 500")


def available() -> dict[str, int]:
    """Row counts of the bundled datasets (for a data-status panel)."""
    return {
        "S&P 500 constituents": len(sp500_constituents()),
        "S&P 500 monthly history": len(spx_history()),
        "US 10Y yield history": len(us10y()),
    }
