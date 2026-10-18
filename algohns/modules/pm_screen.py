"""Reusable Portfolio Management screening — no Streamlit.

Mirrors the Strategy Lab's screen so the 24/7 runner can rebuild a saved PM
strategy's target weights off the same universe and rules the app used. The
app's page keeps its own cached copies; this module is the Streamlit-free path
for the worker.
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

from . import strategy_lab as sl

__all__ = ["CAP_ORDER", "curated_universe", "full_universe", "weights_from_params"]

CAP_ORDER = ["Mega Cap", "Large Cap", "Mid Cap", "Small Cap", "Micro Cap", "Nano Cap"]

# US listing venues (FinanceDatabase `exchange`); Alpaca trades US equities only.
_US_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BATS", "NYS",
                 "NASDAQ", "NYSE"}


@lru_cache(maxsize=1)
def curated_universe() -> pd.DataFrame:
    return sl.demo_universe()


@lru_cache(maxsize=1)
def full_universe() -> pd.DataFrame:
    """FinanceDatabase equities restricted to Alpaca-tradable US listings."""
    from . import universe as un
    if not un.available():
        return pd.DataFrame()
    df = un.search("Equities", limit=1_000_000)
    if df.empty:
        return df
    sym = df["symbol"].astype(str)
    df = df[df["exchange"].isin(_US_EXCHANGES) & sym.str.fullmatch(r"[A-Z]{1,5}")]
    return df.rename(columns={"symbol": "ticker", "market_cap": "cap_category"})


def _criteria_from_dict(d: dict):
    d = d or {}
    return sl.ScreenCriteria(
        beta_min=d.get("beta_min"), beta_max=d.get("beta_max"),
        market_cap_min=d.get("market_cap_min"), market_cap_max=d.get("market_cap_max"),
        max_volatility=d.get("max_volatility"),
        min_dividend_yield=d.get("min_dividend_yield"),
        sectors=tuple(d.get("sectors") or ()),
        exclude_sectors=tuple(d.get("exclude_sectors") or ()),
        countries=tuple(d.get("countries") or ()),
        max_positions=int(d.get("max_positions") or 20),
        expression=d.get("expression") or "",
    )


def weights_from_params(params: dict) -> dict:
    """Rebuild {ticker: weight} from a saved PM strategy's stored params."""
    params = params or {}
    full_db = bool(params.get("full_db"))
    universe = full_universe() if full_db else curated_universe()
    if universe is None or universe.empty:
        return {}
    screen_src = universe
    cap_sel = params.get("cap_sel") or []
    if full_db and "cap_category" in universe.columns:
        if cap_sel:
            screen_src = screen_src[screen_src["cap_category"].isin(cap_sel)]
        rank = {c: i for i, c in enumerate(CAP_ORDER)}
        screen_src = screen_src.assign(
            _caprank=screen_src["cap_category"].map(rank).fillna(99)
        ).sort_values("_caprank")
    try:
        crit = _criteria_from_dict(params.get("criteria", {}))
        picks = sl.screen_universe(screen_src, crit)
    except Exception:  # noqa: BLE001 - a bad saved rule yields no trade, not a crash
        return {}
    return sl.build_weights(picks, params.get("weighting", "equal")) if not picks.empty else {}
