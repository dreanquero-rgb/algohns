"""Shared SEC EDGAR HTTP client.

EDGAR enforces fair access: a descriptive User-Agent with contact info is
mandatory (generic agents get 403) and the request rate is capped around
10/s. Both modules 4 and 5 go through this client so the limit is respected
globally rather than per-module.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import requests

from core.cache import cache_key, cached_json
from core.config import settings

__all__ = ["SecClient", "CompanyRef", "load_company_tickers"]

log = logging.getLogger(__name__)

SEC_BASE = "https://www.sec.gov"
SEC_DATA = "https://data.sec.gov"
TICKER_INDEX = f"{SEC_BASE}/files/company_tickers.json"


@dataclass(frozen=True)
class CompanyRef:
    cik: str          # zero-padded to 10 digits
    ticker: str
    name: str

    @property
    def cik_int(self) -> int:
        return int(self.cik)


class _RateLimiter:
    """Process-wide token spacing, shared across threads."""

    def __init__(self, per_second: float) -> None:
        self._min_interval = 1.0 / max(per_second, 0.1)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            gap = now - self._last
            if gap < self._min_interval:
                time.sleep(self._min_interval - gap)
            self._last = time.monotonic()


class SecClient:
    """Rate-limited, cached EDGAR reader."""

    def __init__(self, user_agent: str | None = None) -> None:
        self.user_agent = user_agent or settings.require_sec_user_agent()
        self._limiter = _RateLimiter(settings.sec_rate_limit_per_sec)
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
        })

    def get_json(self, url: str, *, ttl: int | None = None) -> dict:
        """Fetch and cache a JSON endpoint."""
        def fetch() -> dict:
            self._limiter.wait()
            resp = self._session.get(url, timeout=30)
            if resp.status_code == 403:
                raise RuntimeError(
                    f"EDGAR 403 su {url}. Il User-Agent "
                    f"{self.user_agent!r} e' stato rifiutato: serve un "
                    "contatto reale in SEC_USER_AGENT."
                )
            resp.raise_for_status()
            return resp.json()

        return cached_json(cache_key("sec", url), fetch, ttl=ttl)

    def get_text(self, url: str, *, ttl: int | None = None) -> str:
        """Fetch and cache a text/HTML document."""
        def fetch() -> dict:
            self._limiter.wait()
            resp = self._session.get(url, timeout=60)
            resp.raise_for_status()
            return {"text": resp.text}

        return cached_json(cache_key("sec-text", url), fetch, ttl=ttl).get("text", "")

    # ------------------------------------------------------------- endpoints

    def company_facts(self, cik: str) -> dict:
        """XBRL companyfacts: every tagged fact the filer has reported."""
        return self.get_json(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{cik}.json")

    def submissions(self, cik: str) -> dict:
        """Filing history metadata for one company."""
        return self.get_json(f"{SEC_DATA}/submissions/CIK{cik}.json")

    def recent_filings(
        self, cik: str, forms: tuple[str, ...] = ("10-K", "10-Q"), limit: int = 8
    ) -> list[dict]:
        """Most recent filings of the requested form types, newest first."""
        data = self.submissions(cik)
        recent = data.get("filings", {}).get("recent", {})
        rows = zip(
            recent.get("accessionNumber", []),
            recent.get("form", []),
            recent.get("filingDate", []),
            recent.get("primaryDocument", []),
            recent.get("reportDate", []),
        )
        out: list[dict] = []
        for accession, form, filed, doc, report in rows:
            if form not in forms:
                continue
            acc_nodash = accession.replace("-", "")
            out.append({
                "accession": accession,
                "form": form,
                "filing_date": filed,
                "report_date": report,
                "url": (
                    f"{SEC_BASE}/Archives/edgar/data/{int(cik)}/"
                    f"{acc_nodash}/{doc}"
                ),
            })
            if len(out) >= limit:
                break
        return out


def load_company_tickers(client: SecClient | None = None) -> dict[str, CompanyRef]:
    """Ticker -> CompanyRef for every SEC filer, from the official index."""
    client = client or SecClient()
    # This index changes rarely; cache it for a week.
    raw = client.get_json(TICKER_INDEX, ttl=7 * 24 * 3600)
    out: dict[str, CompanyRef] = {}
    for row in raw.values():
        ticker = str(row.get("ticker", "")).upper()
        if not ticker:
            continue
        out[ticker] = CompanyRef(
            cik=str(row["cik_str"]).zfill(10),
            ticker=ticker,
            name=str(row.get("title", "")),
        )
    return out
