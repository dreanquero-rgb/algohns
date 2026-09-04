"""Module 3 companion — huge financial-instrument universe (FinanceDatabase).

Wraps JerBouma/FinanceDatabase (300k+ instruments across equities, ETFs, funds,
indices, currencies, cryptos and money markets) behind a small search API used
by the backtest suite to pick a tradable universe. The database ships bundled
with the package, so this works fully offline.
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

from ..core.utils import is_available, lazy_import, require

_fd = lazy_import("financedatabase", pip_name="financedatabase",
                  reason="browse the instrument universe")

ASSET_CLASSES = ["Equities", "ETFs", "Funds", "Indices", "Currencies", "Cryptos", "Moneymarkets"]

# Columns worth showing per class (kept compact for the UI table).
_DISPLAY_COLS = {
    "Equities": ["name", "sector", "industry", "country", "currency", "exchange", "market_cap"],
    "ETFs": ["name", "category_group", "category", "family", "currency", "exchange"],
    "Funds": ["name", "category_group", "category", "family", "currency"],
    "Indices": ["name", "currency", "exchange"],
    "Currencies": ["name", "base_currency", "quote_currency"],
    "Cryptos": ["name", "cryptocurrency", "currency"],
    "Moneymarkets": ["name", "currency", "exchange"],
}


@lru_cache(maxsize=None)
def _db(asset_class: str):
    """Instantiate (and cache) a FinanceDatabase class."""
    fd = require(_fd)
    if asset_class not in ASSET_CLASSES:
        raise ValueError(f"unknown asset class {asset_class}")
    return getattr(fd, asset_class)()


def available() -> bool:
    return is_available(_fd)


@lru_cache(maxsize=32)
def options(asset_class: str) -> dict[str, tuple]:
    """Filterable fields -> possible values for an asset class."""
    db = _db(asset_class)
    try:
        opts = db.show_options()
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, tuple] = {}
    if isinstance(opts, dict):
        for field, values in opts.items():
            try:
                vals = [v for v in list(values) if isinstance(v, str) and v]
                out[field] = tuple(sorted(set(vals)))
            except Exception:  # noqa: BLE001
                continue
    return out


def search(
    asset_class: str,
    filters: dict[str, str] | None = None,
    query: str = "",
    limit: int = 500,
) -> pd.DataFrame:
    """Filtered/searched slice of the universe.

    ``filters`` maps a field (from :func:`options`) to a selected value; ``query``
    is a free-text match against symbol + name.
    """
    db = _db(asset_class)
    filters = {k: v for k, v in (filters or {}).items() if v}
    try:
        df = db.select(**filters) if filters else db.select()
    except Exception:  # noqa: BLE001 - unknown filter -> unfiltered
        df = db.select()

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.reset_index()  # symbol becomes a column
    sym_col = df.columns[0]
    df = df.rename(columns={sym_col: "symbol"})

    if query:
        q = query.lower()
        name = df["name"].astype(str).str.lower() if "name" in df else ""
        mask = df["symbol"].astype(str).str.lower().str.contains(q, na=False)
        if "name" in df:
            mask = mask | name.str.contains(q, na=False)
        df = df[mask]

    cols = ["symbol"] + [c for c in _DISPLAY_COLS.get(asset_class, []) if c in df.columns]
    return df[cols].head(limit).reset_index(drop=True)


def tickers_from(df: pd.DataFrame) -> list[str]:
    """Extract the ticker symbols from a search result."""
    if df.empty or "symbol" not in df:
        return []
    return [str(s) for s in df["symbol"].tolist() if str(s) and str(s) != "nan"]
