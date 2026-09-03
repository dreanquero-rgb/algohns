"""Module 5 — Consolidated SEC Financial Statements Aggregator.

Pulls standardised XBRL financial data straight from the SEC's
``companyfacts`` REST API (``data.sec.gov/api/xbrl/companyfacts``) and
normalises it into three tidy statements — Income Statement, Balance Sheet,
Cash Flow — so any set of S&P 500 tickers can be compared side-by-side without
opening a single 10-K by hand.

No filing download or parsing is required: XBRL company-facts are the same
structured numbers companies tag in their filings, served as JSON.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import pandas as pd

from ..config import get_settings
from ..core.utils import lazy_import, require

_requests = lazy_import("requests", pip_name="requests", reason="query SEC EDGAR")

# Map of statement -> {display label: [candidate US-GAAP XBRL tags]}.
# Multiple candidates handle tag drift across issuers/years; first hit wins.
STATEMENT_TAGS: dict[str, dict[str, list[str]]] = {
    "income_statement": {
        "Revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
        "Cost of Revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
        "Gross Profit": ["GrossProfit"],
        "Operating Income": ["OperatingIncomeLoss"],
        "Net Income": ["NetIncomeLoss"],
        "EPS (Diluted)": ["EarningsPerShareDiluted"],
        "R&D Expense": ["ResearchAndDevelopmentExpense"],
    },
    "balance_sheet": {
        "Total Assets": ["Assets"],
        "Total Liabilities": ["Liabilities"],
        "Cash & Equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
        "Total Equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
        "Long-Term Debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
        "Current Assets": ["AssetsCurrent"],
        "Current Liabilities": ["LiabilitiesCurrent"],
    },
    "cash_flow": {
        "Operating Cash Flow": ["NetCashProvidedByUsedInOperatingActivities"],
        "Investing Cash Flow": ["NetCashProvidedByUsedInInvestingActivities"],
        "Financing Cash Flow": ["NetCashProvidedByUsedInFinancingActivities"],
        "CapEx": ["PaymentsToAcquirePropertyPlantAndEquipment"],
        "Dividends Paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    },
}


@dataclass
class CompanyFacts:
    ticker: str
    cik: str
    entity_name: str
    raw: dict = field(default_factory=dict)


class SECAggregator:
    """Fetch and normalise XBRL company facts from EDGAR."""

    BASE = "https://data.sec.gov"

    def __init__(self) -> None:
        self.headers = {"User-Agent": get_settings().sec_user_agent, "Accept-Encoding": "gzip, deflate"}

    # ----------------------------------------------------------- ticker->CIK
    @lru_cache(maxsize=1)
    def _ticker_map(self) -> dict[str, str]:
        requests = require(_requests)
        data = requests.get(
            "https://www.sec.gov/files/company_tickers.json", headers=self.headers, timeout=30
        ).json()
        return {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in data.values()}

    def cik_for(self, ticker: str) -> str:
        cik = self._ticker_map().get(ticker.upper())
        if not cik:
            raise ValueError(f"ticker {ticker} not found in EDGAR ticker map")
        return cik

    # ------------------------------------------------------------- fetch
    def company_facts(self, ticker: str) -> CompanyFacts:
        requests = require(_requests)
        cik = self.cik_for(ticker)
        url = f"{self.BASE}/api/xbrl/companyfacts/CIK{cik}.json"
        resp = requests.get(url, headers=self.headers, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        return CompanyFacts(
            ticker=ticker.upper(),
            cik=cik,
            entity_name=payload.get("entityName", ticker.upper()),
            raw=payload,
        )

    # --------------------------------------------------------- normalisation
    @staticmethod
    def _latest_annual(fact: dict) -> tuple[str | None, float | None]:
        """Return (fiscal-year, value) for the most recent annual (FY) datapoint."""
        units = fact.get("units", {})
        best_period, best_val, best_end = None, None, ""
        for _unit, entries in units.items():
            for e in entries:
                if e.get("form") not in ("10-K", "20-F"):
                    continue
                if e.get("fp") not in (None, "FY"):
                    continue
                end = e.get("end", "")
                if end > best_end:
                    best_end, best_val, best_period = end, e.get("val"), e.get("fy")
        return (str(best_period) if best_period else best_end[:4] or None, best_val)

    def statement(self, facts: CompanyFacts, statement: str) -> dict[str, float | None]:
        """Extract one normalised statement (label -> latest annual value)."""
        gaap = facts.raw.get("facts", {}).get("us-gaap", {})
        result: dict[str, float | None] = {}
        for label, candidates in STATEMENT_TAGS[statement].items():
            value = None
            for tag in candidates:
                if tag in gaap:
                    _, value = self._latest_annual(gaap[tag])
                    if value is not None:
                        break
            result[label] = value
        return result

    def timeseries(self, facts: CompanyFacts, tag: str, unit: str = "USD") -> pd.Series:
        """Annual time-series for a single XBRL tag (for trend charts)."""
        gaap = facts.raw.get("facts", {}).get("us-gaap", {})
        if tag not in gaap:
            return pd.Series(dtype=float)
        rows: dict[str, float] = {}
        for e in gaap[tag].get("units", {}).get(unit, []):
            if e.get("form") in ("10-K", "20-F") and e.get("fp") in (None, "FY"):
                fy = e.get("fy")
                if fy is not None:
                    rows[str(fy)] = e.get("val")
        return pd.Series(rows).sort_index()

    # ------------------------------------------------------------ comparison
    def compare(self, tickers: list[str], statement: str = "income_statement") -> pd.DataFrame:
        """Side-by-side statement comparison across tickers (columns = tickers)."""
        cols = {}
        for t in tickers:
            try:
                facts = self.company_facts(t)
                cols[facts.ticker] = self.statement(facts, statement)
            except Exception as exc:  # noqa: BLE001
                cols[t.upper()] = {"error": str(exc)}
        df = pd.DataFrame(cols)
        df.index.name = statement.replace("_", " ").title()
        return df

    def compare_all(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        """Fetch every statement once per ticker and return all three frames."""
        cache: dict[str, CompanyFacts] = {}
        for t in tickers:
            try:
                cache[t.upper()] = self.company_facts(t)
            except Exception:  # noqa: BLE001
                cache[t.upper()] = None  # type: ignore[assignment]
        out: dict[str, pd.DataFrame] = {}
        for stmt in STATEMENT_TAGS:
            cols = {}
            for t, facts in cache.items():
                cols[t] = self.statement(facts, stmt) if facts else {}
            frame = pd.DataFrame(cols)
            frame.index.name = stmt.replace("_", " ").title()
            out[stmt] = frame
        return out

    # ------------------------------------------------------------- ratios
    def key_ratios(self, facts: CompanyFacts) -> dict[str, float | None]:
        """A handful of comparison-friendly ratios derived from the statements."""
        inc = self.statement(facts, "income_statement")
        bs = self.statement(facts, "balance_sheet")

        def ratio(a, b):
            try:
                return round(a / b, 4) if (a is not None and b) else None
            except Exception:  # noqa: BLE001
                return None

        return {
            "Net Margin": ratio(inc.get("Net Income"), inc.get("Revenue")),
            "Gross Margin": ratio(inc.get("Gross Profit"), inc.get("Revenue")),
            "ROE": ratio(inc.get("Net Income"), bs.get("Total Equity")),
            "ROA": ratio(inc.get("Net Income"), bs.get("Total Assets")),
            "Current Ratio": ratio(bs.get("Current Assets"), bs.get("Current Liabilities")),
            "Debt/Equity": ratio(bs.get("Long-Term Debt"), bs.get("Total Equity")),
        }
