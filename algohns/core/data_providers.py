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
from .utils import is_available, lazy_import, require

_yf = lazy_import("yfinance", pip_name="yfinance", reason="download market prices")
_pdr = lazy_import("pandas_datareader.data", pip_name="pandas-datareader",
                   reason="download long history from Stooq (back to the 1990s)")

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
    @staticmethod
    def _normalize(tickers: str | list[str]) -> list[str]:
        if isinstance(tickers, str):
            return [t.strip().upper() for t in tickers.replace(",", " ").split() if t.strip()]
        return [str(t).strip().upper() for t in tickers if str(t).strip()]

    def history(
        self,
        tickers: str | list[str],
        period: str = "5y",
        interval: str = "1d",
        auto_adjust: bool = True,
        source: str = "yfinance",
        start: str | None = None,
    ) -> pd.DataFrame:
        """Return adjusted close prices as a DataFrame (columns = tickers).

        ``source='stooq'`` fetches long history (often back to the 1990s) via
        pandas-datareader; ``start`` (YYYY-MM-DD) bounds it.
        """
        tickers = self._normalize(tickers)
        if source == "stooq":
            return self._history_stooq(tickers, start or "1990-01-01")

        yf = require(_yf)
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

    def _history_stooq(self, tickers: list[str], start: str) -> pd.DataFrame:
        """Long-history close prices from Stooq (data back to the 1990s)."""
        pdr = require(_pdr)
        key = f"stooq:{'|'.join(sorted(tickers))}:{start}"
        path = self._cache_path(key)
        cached = self._read_cache(path)
        if cached is not None:
            return cached
        frames = {}
        for t in tickers:
            try:
                df = pdr.DataReader(t, "stooq", start=start)
                if not df.empty and "Close" in df:
                    frames[t] = df["Close"].sort_index()
            except Exception:  # noqa: BLE001
                continue
        prices = pd.DataFrame(frames).dropna(how="all").ffill().dropna(how="all")
        self._write_cache(path, prices)
        return prices

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
