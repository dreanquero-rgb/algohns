"""Price data loading with a disk cache and a synthetic fallback.

Providers are tried in order and the first that returns usable data wins.
`synthetic=True` generates reproducible correlated series so the whole
platform is demoable and testable without network access or API keys —
clearly labelled, because a synthetic backtest that looks real is worse
than no backtest.
"""
from __future__ import annotations

import logging
import zlib
from datetime import date, timedelta

import numpy as np
import pandas as pd

from core.cache import cache_key, cached_json

__all__ = ["load_prices", "PriceLoadResult", "synthetic_prices", "SP500_SAMPLE"]

log = logging.getLogger(__name__)

# A sector-spread S&P 500 sample, used as the default universe in the UI.
SP500_SAMPLE = {
    "AAPL": "Information Technology", "MSFT": "Information Technology",
    "NVDA": "Information Technology", "AVGO": "Information Technology",
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary", "GOOGL": "Communication Services",
    "META": "Communication Services", "DIS": "Communication Services",
    "JPM": "Financials", "BAC": "Financials", "BRK-B": "Financials",
    "JNJ": "Health Care", "UNH": "Health Care", "LLY": "Health Care",
    "PG": "Consumer Staples", "KO": "Consumer Staples", "WMT": "Consumer Staples",
    "XOM": "Energy", "CVX": "Energy",
    "CAT": "Industrials", "GE": "Industrials", "UNP": "Industrials",
    "LIN": "Materials", "NEE": "Utilities", "AMT": "Real Estate",
}


class PriceLoadResult:
    """Prices plus provenance, so the UI can say where the numbers came from."""

    def __init__(
        self, prices: pd.DataFrame, source: str, warnings: list[str] | None = None
    ) -> None:
        self.prices = prices
        self.source = source
        self.warnings = warnings or []

    @property
    def is_synthetic(self) -> bool:
        return self.source == "synthetic"

    @property
    def tickers(self) -> list[str]:
        return list(self.prices.columns)

    def __repr__(self) -> str:
        return (
            f"PriceLoadResult(source={self.source!r}, "
            f"shape={self.prices.shape}, warnings={len(self.warnings)})"
        )


def synthetic_prices(
    tickers: list[str],
    start: date,
    end: date,
    *,
    seed: int = 20260916,
    annual_drift: float = 0.07,
    annual_vol: float = 0.22,
    mean_correlation: float = 0.45,
) -> pd.DataFrame:
    """Reproducible correlated GBM price paths.

    Correlation is induced through a single common factor plus idiosyncratic
    noise, which reproduces the one property that matters for portfolio
    maths: a covariance matrix that is not diagonal.
    """
    if not tickers:
        raise ValueError("nessun ticker richiesto")
    if end <= start:
        raise ValueError(f"end ({end}) deve seguire start ({start})")

    idx = pd.bdate_range(start, end)
    n_obs, n_assets = len(idx), len(tickers)
    if n_obs < 2:
        raise ValueError(f"intervallo troppo corto: {n_obs} giorni lavorativi")

    beta = np.sqrt(max(0.0, min(mean_correlation, 0.99)))
    # One shared market factor, drawn from the base seed.
    common = np.random.default_rng(seed).standard_normal((n_obs, 1))

    # Each ticker draws its idiosyncratic stream and its vol/drift profile
    # from its *own* seeded generator, keyed on the ticker name. Two
    # consequences that matter:
    #
    #  * crc32, not hash(): Python randomises string hashing per process
    #    (PYTHONHASHSEED), so hash() would produce a different
    #    "reproducible" dataset on every run and silently break backtest
    #    determinism.
    #  * per-ticker streams make the result order-independent: adding a
    #    name to the universe, or reordering it, leaves every other
    #    ticker's path untouched, so universe A and universe B stay
    #    comparable.
    idio = np.empty((n_obs, n_assets))
    spread = np.empty(n_assets)
    for j, t in enumerate(tickers):
        token = zlib.crc32(t.encode())
        tick_rng = np.random.default_rng((int(seed), int(token)))
        idio[:, j] = tick_rng.standard_normal(n_obs)
        spread[j] = 0.6 + 1.2 * ((token % 1000) / 1000.0)

    shocks = beta * common + np.sqrt(1.0 - beta**2) * idio
    vol_d = annual_vol * spread / np.sqrt(252)
    mu_d = annual_drift * (0.5 + spread / 2.0) / 252

    rets = mu_d + shocks * vol_d
    paths = 100.0 * np.cumprod(1.0 + rets, axis=0)
    return pd.DataFrame(paths, index=idx, columns=tickers)


def _load_yfinance(tickers: list[str], start: date, end: date) -> pd.DataFrame | None:
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return None
    try:
        raw = yf.download(
            tickers, start=start, end=end, auto_adjust=True,
            progress=False, threads=True,
        )
    except Exception as exc:
        log.warning("yfinance download failed: %s", exc)
        return None
    if raw is None or len(raw) == 0:
        return None

    # yfinance returns a MultiIndex for multi-ticker requests.
    if isinstance(raw.columns, pd.MultiIndex):
        field = "Close" if "Close" in raw.columns.get_level_values(0) else raw.columns[0][0]
        px = raw[field]
    else:
        px = raw[["Close"]] if "Close" in raw.columns else raw
        px.columns = tickers[:1]
    return px.dropna(how="all")


def load_prices(
    tickers: list[str],
    start: date | None = None,
    end: date | None = None,
    *,
    synthetic: bool = False,
    min_observations: int = 60,
    use_cache: bool = True,
) -> PriceLoadResult:
    """Load adjusted close prices for `tickers`.

    Falls back to synthetic data when no provider yields enough history,
    and always reports which path was taken via `PriceLoadResult.source`.
    Tickers with fewer than `min_observations` points are dropped rather
    than forward-filled, because interpolated history silently corrupts
    covariance estimates.
    """
    tickers = [t.strip().upper() for t in tickers if t and t.strip()]
    if not tickers:
        raise ValueError("nessun ticker richiesto")
    tickers = list(dict.fromkeys(tickers))  # de-dup, keep order

    end = end or date.today()
    start = start or (end - timedelta(days=365 * 3))
    warnings: list[str] = []

    if synthetic:
        return PriceLoadResult(
            synthetic_prices(tickers, start, end), "synthetic",
            ["Dati sintetici: utili per demo e test, non per decisioni reali."],
        )

    def fetch() -> dict:
        px = _load_yfinance(tickers, start, end)
        if px is None or px.empty:
            return {}
        return {
            "index": [d.isoformat() for d in px.index.date],
            "columns": list(px.columns),
            "values": px.to_numpy().tolist(),
        }

    payload: dict = {}
    if use_cache:
        key = cache_key("prices", ",".join(tickers), start, end)
        payload = cached_json(key, fetch) or {}
    else:
        payload = fetch()

    if payload.get("values"):
        px = pd.DataFrame(
            payload["values"],
            index=pd.to_datetime(payload["index"]),
            columns=payload["columns"],
        ).astype(float)

        usable = [c for c in px.columns if px[c].notna().sum() >= min_observations]
        dropped = sorted(set(px.columns) - set(usable))
        if dropped:
            warnings.append(
                f"Scartati per storico insufficiente (<{min_observations} punti): "
                f"{', '.join(dropped)}"
            )
        if usable:
            px = px[usable].dropna(how="all")
            missing = sorted(set(tickers) - set(usable))
            if missing:
                warnings.append(f"Ticker non recuperati: {', '.join(missing)}")
            return PriceLoadResult(px, "yfinance", warnings)

    warnings.append(
        "Nessun provider di mercato disponibile (yfinance assente o rete "
        "bloccata): passo a dati sintetici."
    )
    return PriceLoadResult(
        synthetic_prices(tickers, start, end), "synthetic", warnings
    )
