"""Unified market-data access layer.

A thin abstraction over yfinance (and, when configured, Alpaca market data)
so every module speaks the same OHLCV / price language. Results are cached on
disk to keep the Streamlit dashboard snappy and avoid hammering providers.
"""
from __future__ import annotations

import hashlib
import time
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ..config import get_settings
from .utils import lazy_import, require

_yf = lazy_import("yfinance", pip_name="yfinance", reason="download market prices")

_CACHE_TTL_SECONDS = 60 * 30  # 30 minutes


class MarketData:
    """Price and fundamentals provider with a simple parquet disk cache."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        settings = get_settings()
        self.cache_dir = cache_dir or settings.cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ cache
    def _cache_path(self, key: str) -> Path:
        digest = hashlib.sha1(key.encode()).hexdigest()[:16]
        return self.cache_dir / f"px_{digest}.parquet"

    def _read_cache(self, path: Path) -> pd.DataFrame | None:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > _CACHE_TTL_SECONDS:
            return None
        try:
            return pd.read_parquet(path)
        except Exception:  # noqa: BLE001
            return None

    def _write_cache(self, path: Path, df: pd.DataFrame) -> None:
        try:
            df.to_parquet(path)
        except Exception:  # noqa: BLE001 - parquet engine may be missing; ignore
            pass

    # ----------------------------------------------------------------- prices
    def history(
        self,
        tickers: str | list[str],
        period: str = "5y",
        interval: str = "1d",
        auto_adjust: bool = True,
    ) -> pd.DataFrame:
        """Return adjusted close prices as a DataFrame (columns = tickers)."""
        yf = require(_yf)
        if isinstance(tickers, str):
            tickers = [t.strip().upper() for t in tickers.replace(",", " ").split() if t.strip()]
        key = f"{'|'.join(sorted(tickers))}:{period}:{interval}:{auto_adjust}"
        path = self._cache_path(key)
        cached = self._read_cache(path)
        if cached is not None:
            return cached

        raw = yf.download(
            tickers=tickers,
            period=period,
            interval=interval,
            auto_adjust=auto_adjust,
            progress=False,
            group_by="column",
            threads=True,
        )
        prices = self._extract_close(raw, tickers)
        prices = prices.dropna(how="all").ffill().dropna(how="all")
        self._write_cache(path, prices)
        return prices

    @staticmethod
    def _extract_close(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
        if raw is None or raw.empty:
            return pd.DataFrame()
        if isinstance(raw.columns, pd.MultiIndex):
            field = "Close" if "Close" in raw.columns.get_level_values(0) else raw.columns.levels[0][0]
            close = raw[field]
        else:
            # Single ticker download -> flat columns.
            close = raw[["Close"]] if "Close" in raw.columns else raw
            if len(tickers) == 1:
                close.columns = tickers
        return close

    def returns(self, tickers: str | list[str], period: str = "5y") -> pd.DataFrame:
        """Daily simple returns for the requested tickers."""
        px = self.history(tickers, period=period)
        return px.pct_change().dropna(how="all")

    # ------------------------------------------------------------ fundamentals
    def info(self, ticker: str) -> dict:
        """Best-effort fundamentals snapshot for a single ticker."""
        yf = require(_yf)
        try:
            t = yf.Ticker(ticker)
            return dict(t.info)
        except Exception:  # noqa: BLE001
            return {}


@lru_cache(maxsize=1)
def get_market_data() -> MarketData:
    """Shared MarketData instance (cached)."""
    return MarketData()
