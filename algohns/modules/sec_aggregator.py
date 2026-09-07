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
        "R&D Expense": ["ResearchAndDevelopmentExpense"],
        "SG&A Expense": ["SellingGeneralAndAdministrativeExpense"],
        "Operating Expenses": ["OperatingExpenses", "CostsAndExpenses"],
        "Operating Income": ["OperatingIncomeLoss"],
        "Interest Expense": ["InterestExpense", "InterestExpenseNonoperating"],
        "Pre-tax Income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
        "Income Tax": ["IncomeTaxExpenseBenefit"],
        "Net Income": ["NetIncomeLoss"],
        "EPS (Basic)": ["EarningsPerShareBasic"],
        "EPS (Diluted)": ["EarningsPerShareDiluted"],
        "Shares (Diluted)": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    },
    "balance_sheet": {
        "Cash & Equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
        "Short-term Investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent"],
        "Receivables": ["AccountsReceivableNetCurrent"],
        "Inventory": ["InventoryNet"],
        "Current Assets": ["AssetsCurrent"],
        "PP&E (net)": ["PropertyPlantAndEquipmentNet"],
        "Goodwill": ["Goodwill"],
        "Total Assets": ["Assets"],
        "Accounts Payable": ["AccountsPayableCurrent"],
        "Current Liabilities": ["LiabilitiesCurrent"],
        "Long-Term Debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
        "Total Liabilities": ["Liabilities"],
        "Retained Earnings": ["RetainedEarningsAccumulatedDeficit"],
        "Total Equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    },
    "cash_flow": {
        "Operating Cash Flow": ["NetCashProvidedByUsedInOperatingActivities"],
        "Depreciation & Amort.": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet"],
        "Stock-based Comp": ["ShareBasedCompensation"],
        "CapEx": ["PaymentsToAcquirePropertyPlantAndEquipment"],
        "Investing Cash Flow": ["NetCashProvidedByUsedInInvestingActivities"],
        "Financing Cash Flow": ["NetCashProvidedByUsedInFinancingActivities"],
        "Dividends Paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
        "Share Repurchases": ["PaymentsForRepurchaseOfCommonStock"],
        "Net Change in Cash": ["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect"],
    },
}

# Headline numbers shown as prominent KPI tiles.
KPI_TAGS: dict[str, list[str]] = {
    "Revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "Net Income": ["NetIncomeLoss"],
    "Operating Income": ["OperatingIncomeLoss"],
    "Total Assets": ["Assets"],
    "Total Equity": ["StockholdersEquity"],
    "Operating Cash Flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "EPS (Diluted)": ["EarningsPerShareDiluted"],
    "Long-Term Debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
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
        """Resolve a ticker to its zero-padded CIK.

        Prefers the bundled real S&P 500 CIK map (offline, no network) and only
        falls back to SEC's live ticker file for names outside the index.
        """
        from .reference_data import cik_map

        cik = cik_map().get(ticker.upper())
        if cik:
            return cik
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

    def _annual_by_year(self, fact: dict) -> dict[str, float]:
        """All annual (FY) values for one XBRL fact, keyed by fiscal year."""
        rows: dict[str, float] = {}
        for _unit, entries in fact.get("units", {}).items():
            for e in entries:
                if e.get("form") in ("10-K", "20-F") and e.get("fp") in (None, "FY"):
                    fy = e.get("fy")
                    end = e.get("end", "")
                    if fy is not None and e.get("val") is not None:
                        # Keep the datapoint whose period end is latest for that FY.
                        key = str(fy)
                        if key not in rows or end >= rows.get(f"_end_{key}", ""):
                            rows[key] = e.get("val")
                            rows[f"_end_{key}"] = end
        return {k: v for k, v in rows.items() if not k.startswith("_end_")}

    def full_statement(self, facts: CompanyFacts, statement: str, years: int = 5) -> pd.DataFrame:
        """Full statement as a multi-year table (rows = line items, cols = FY)."""
        gaap = facts.raw.get("facts", {}).get("us-gaap", {})
        data: dict[str, dict[str, float]] = {}
        for label, candidates in STATEMENT_TAGS[statement].items():
            series: dict[str, float] = {}
            for tag in candidates:
                if tag in gaap:
                    series = self._annual_by_year(gaap[tag])
                    if series:
                        break
            data[label] = series
        df = pd.DataFrame(data).T  # rows = line items, cols = years
        if df.empty:
            return df
        # Keep the most recent `years` columns, sorted ascending.
        cols = sorted([c for c in df.columns], key=lambda x: str(x))[-years:]
        df = df[cols]
        df.index.name = statement.replace("_", " ").title()
        return df

    def kpis(self, facts: CompanyFacts) -> dict[str, dict]:
        """Headline KPIs with latest value and YoY change for tiles."""
        gaap = facts.raw.get("facts", {}).get("us-gaap", {})
        out: dict[str, dict] = {}
        for label, candidates in KPI_TAGS.items():
            series = {}
            for tag in candidates:
                if tag in gaap:
                    series = self._annual_by_year(gaap[tag])
                    if series:
                        break
            if not series:
                out[label] = {"value": None, "yoy": None, "year": None}
                continue
            yrs = sorted(series.keys())
            latest = series[yrs[-1]]
            prev = series[yrs[-2]] if len(yrs) > 1 else None
            yoy = None
            if prev not in (None, 0) and latest is not None:
                yoy = (latest - prev) / abs(prev)
            out[label] = {"value": latest, "yoy": yoy, "year": yrs[-1]}
        return out

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


def sample_facts(ticker: str = "DEMO") -> CompanyFacts:
    """A complete multi-year company-facts fixture for offline demo.

    Every line item of all three statements is populated, in **absolute USD**
    (the same scale SEC XBRL uses), so the offline view looks exactly like the
    live one. Used only when data.sec.gov is unreachable.
    """
    years = [2020, 2021, 2022, 2023, 2024]
    M = 1_000_000  # figures below are in millions -> stored in absolute USD

    def _fy(values, unit="USD", scale=M):
        return {"units": {unit: [
            {"form": "10-K", "fp": "FY", "fy": y, "end": f"{y}-09-28", "val": v * scale}
            for y, v in zip(years, values)
        ]}}

    def _raw(values, unit="USD/shares"):
        return _fy(values, unit=unit, scale=1)

    gaap = {
        # --- Income statement -------------------------------------------------
        "Revenues": _fy([274_515, 365_817, 394_328, 383_285, 391_035]),
        "CostOfRevenue": _fy([169_559, 212_981, 223_546, 214_137, 210_352]),
        "GrossProfit": _fy([104_956, 152_836, 170_782, 169_148, 180_683]),
        "ResearchAndDevelopmentExpense": _fy([18_752, 21_914, 26_251, 29_915, 31_370]),
        "SellingGeneralAndAdministrativeExpense": _fy([19_916, 21_973, 25_094, 24_932, 26_097]),
        "OperatingExpenses": _fy([38_668, 43_887, 51_345, 54_847, 57_467]),
        "OperatingIncomeLoss": _fy([66_288, 108_949, 119_437, 114_301, 123_216]),
        "InterestExpense": _fy([2_873, 2_645, 2_931, 3_933, 4_100]),
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest":
            _fy([67_091, 109_207, 119_103, 113_736, 123_485]),
        "IncomeTaxExpenseBenefit": _fy([9_680, 14_527, 19_300, 16_741, 29_749]),
        "NetIncomeLoss": _fy([57_411, 94_680, 99_803, 96_995, 93_736]),
        "EarningsPerShareBasic": _raw([3.31, 5.67, 6.15, 6.16, 6.11]),
        "EarningsPerShareDiluted": _raw([3.28, 5.61, 6.11, 6.13, 6.08]),
        "WeightedAverageNumberOfDilutedSharesOutstanding":
            _fy([17_528, 16_865, 16_326, 15_813, 15_408], unit="shares", scale=1_000),
        # --- Balance sheet ----------------------------------------------------
        "CashAndCashEquivalentsAtCarryingValue": _fy([38_016, 34_940, 23_646, 29_965, 29_943]),
        "ShortTermInvestments": _fy([52_927, 27_699, 24_658, 31_590, 35_228]),
        "AccountsReceivableNetCurrent": _fy([16_120, 26_278, 28_184, 29_508, 33_410]),
        "InventoryNet": _fy([4_061, 6_580, 4_946, 6_331, 7_286]),
        "AssetsCurrent": _fy([143_713, 134_836, 135_405, 143_566, 152_987]),
        "PropertyPlantAndEquipmentNet": _fy([36_766, 39_440, 42_117, 43_715, 45_680]),
        "Goodwill": _fy([0, 0, 0, 0, 0]),
        "Assets": _fy([323_888, 351_002, 352_755, 352_583, 364_980]),
        "AccountsPayableCurrent": _fy([42_296, 54_763, 64_115, 62_611, 68_960]),
        "LiabilitiesCurrent": _fy([105_392, 125_481, 153_982, 145_308, 176_392]),
        "LongTermDebtNoncurrent": _fy([98_667, 109_106, 98_959, 95_281, 85_750]),
        "Liabilities": _fy([258_549, 287_912, 302_083, 290_437, 308_030]),
        "RetainedEarningsAccumulatedDeficit": _fy([14_966, 5_562, -3_068, -214, -19_154]),
        "StockholdersEquity": _fy([65_339, 63_090, 50_672, 62_146, 56_950]),
        # --- Cash flow --------------------------------------------------------
        "NetCashProvidedByUsedInOperatingActivities": _fy([80_674, 104_038, 122_151, 110_543, 118_254]),
        "DepreciationDepletionAndAmortization": _fy([11_056, 11_284, 11_104, 11_519, 11_445]),
        "ShareBasedCompensation": _fy([6_829, 7_906, 9_038, 10_833, 11_688]),
        "PaymentsToAcquirePropertyPlantAndEquipment": _fy([7_309, 11_085, 10_708, 10_959, 9_447]),
        "NetCashProvidedByUsedInInvestingActivities": _fy([-4_289, -14_545, -22_354, 3_705, 2_935]),
        "NetCashProvidedByUsedInFinancingActivities": _fy([-86_820, -93_353, -110_749, -108_488, -121_983]),
        "PaymentsOfDividendsCommonStock": _fy([14_081, 14_467, 14_841, 15_025, 15_234]),
        "PaymentsForRepurchaseOfCommonStock": _fy([72_358, 85_971, 89_402, 77_550, 94_949]),
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect":
            _fy([-10_435, -3_860, -10_952, 5_760, -794]),
    }
    return CompanyFacts(ticker=ticker.upper(), cik="0000000000",
                        entity_name=f"{ticker.upper()} (sample data)",
                        raw={"facts": {"us-gaap": gaap}})
