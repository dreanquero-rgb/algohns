"""MODULE 5 — Consolidated SEC Financial Statements Aggregator.

Normalises XBRL `companyfacts` into comparable Income Statement, Balance
Sheet and Cash Flow views, and lines several tickers up side by side.

The real engineering problem here is **tag heterogeneity**, not fetching.
US-GAAP lets filers express the same concept with different elements:
revenue shows up as `Revenues`, `RevenueFromContractWithCustomerExcluding
AssessedTax`, `SalesRevenueNet` or `RevenueFromContractWithCustomer
IncludingAssessedTax` depending on the filer and the year. Reading a single
tag silently returns NaN for half the S&P 500, and a comparison table full
of holes looks like missing data rather than a mapping bug.

Each line item therefore declares an ordered fallback chain, and every
resolved value records which tag produced it so a surprising number can be
traced back to its element.

Duration facts (revenue, net income) and instant facts (assets, cash) are
selected differently: durations must match the fiscal period length, or a
quarterly figure gets compared against an annual one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from modules.sec_client import CompanyRef, SecClient, load_company_tickers

__all__ = [
    "Statement",
    "FactKind",
    "LineItem",
    "FinancialFact",
    "StatementData",
    "SecAggregator",
    "INCOME_STATEMENT",
    "BALANCE_SHEET",
    "CASH_FLOW",
]

log = logging.getLogger(__name__)


class Statement(str, Enum):
    INCOME = "income_statement"
    BALANCE = "balance_sheet"
    CASHFLOW = "cash_flow"


class FactKind(str, Enum):
    DURATION = "duration"   # flows: revenue, net income, capex
    INSTANT = "instant"     # stocks: assets, cash, equity


@dataclass(frozen=True)
class LineItem:
    """One normalised statement line and the tags that can supply it."""

    label: str
    tags: tuple[str, ...]
    kind: FactKind = FactKind.DURATION
    unit: str = "USD"
    # Negate the reported sign. Some tags are reported as positive costs.
    negate: bool = False

    def __post_init__(self) -> None:
        if not self.tags:
            raise ValueError(f"{self.label}: serve almeno un tag XBRL")


# Ordered most-specific first: post-ASC-606 elements before legacy ones.
INCOME_STATEMENT: tuple[LineItem, ...] = (
    LineItem("Revenue", (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    )),
    LineItem("Cost of Revenue", (
        "CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold",
    )),
    LineItem("Gross Profit", ("GrossProfit",)),
    LineItem("R&D Expense", ("ResearchAndDevelopmentExpense",)),
    LineItem("SG&A Expense", (
        "SellingGeneralAndAdministrativeExpense",
        "GeneralAndAdministrativeExpense",
    )),
    LineItem("Operating Income", (
        "OperatingIncomeLoss", "IncomeLossFromContinuingOperations",
    )),
    LineItem("Interest Expense", (
        "InterestExpense", "InterestExpenseDebt",
        "InterestIncomeExpenseNet",
    )),
    LineItem("Pretax Income", (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    )),
    LineItem("Income Tax", ("IncomeTaxExpenseBenefit",)),
    LineItem("Net Income", (
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    )),
    LineItem("EPS Diluted", ("EarningsPerShareDiluted",), unit="USD/shares"),
    LineItem("Shares Diluted", (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ), unit="shares"),
)

BALANCE_SHEET: tuple[LineItem, ...] = (
    LineItem("Cash & Equivalents", (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ), FactKind.INSTANT),
    LineItem("Short-term Investments", (
        "ShortTermInvestments", "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
    ), FactKind.INSTANT),
    LineItem("Receivables", (
        "AccountsReceivableNetCurrent", "ReceivablesNetCurrent",
    ), FactKind.INSTANT),
    LineItem("Inventory", (
        "InventoryNet", "InventoryFinishedGoodsNetOfReserves",
    ), FactKind.INSTANT),
    LineItem("Total Current Assets", ("AssetsCurrent",), FactKind.INSTANT),
    LineItem("Total Assets", ("Assets",), FactKind.INSTANT),
    LineItem("Total Current Liabilities", ("LiabilitiesCurrent",), FactKind.INSTANT),
    LineItem("Long-term Debt", (
        "LongTermDebtNoncurrent", "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligations",
    ), FactKind.INSTANT),
    LineItem("Total Liabilities", ("Liabilities",), FactKind.INSTANT),
    LineItem("Stockholders Equity", (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ), FactKind.INSTANT),
)

CASH_FLOW: tuple[LineItem, ...] = (
    LineItem("Operating Cash Flow", (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    )),
    LineItem("Capital Expenditure", (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ), negate=True),
    LineItem("Investing Cash Flow", (
        "NetCashProvidedByUsedInInvestingActivities",
        "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
    )),
    LineItem("Financing Cash Flow", (
        "NetCashProvidedByUsedInFinancingActivities",
        "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations",
    )),
    LineItem("Dividends Paid", (
        "PaymentsOfDividendsCommonStock", "PaymentsOfDividends",
    ), negate=True),
    LineItem("Share Repurchases", (
        "PaymentsForRepurchaseOfCommonStock",
    ), negate=True),
    LineItem("Depreciation & Amortization", (
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet", "Depreciation",
    )),
)

STATEMENT_ITEMS = {
    Statement.INCOME: INCOME_STATEMENT,
    Statement.BALANCE: BALANCE_SHEET,
    Statement.CASHFLOW: CASH_FLOW,
}


@dataclass(frozen=True)
class FinancialFact:
    """One resolved value, with the tag it came from."""

    value: float
    tag: str
    fiscal_year: int
    fiscal_period: str
    end_date: str
    form: str
    unit: str

    @property
    def is_annual(self) -> bool:
        return self.fiscal_period == "FY"


@dataclass
class StatementData:
    ticker: str
    company_name: str
    statement: Statement
    # label -> fiscal_year -> fact
    facts: dict[str, dict[int, FinancialFact]] = field(default_factory=dict)
    missing_items: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_frame(self, *, scale: float = 1e6) -> pd.DataFrame:
        """Label x fiscal-year table. `scale` divides monetary values.

        Per-share and share-count lines are never scaled — dividing EPS by a
        million is how a comparison table ends up with a column of zeros.
        """
        if not self.facts:
            return pd.DataFrame()
        years = sorted({y for per_year in self.facts.values() for y in per_year})
        rows: dict[str, dict[int, float]] = {}
        for label, per_year in self.facts.items():
            unscaled = any(
                per_year[y].unit != "USD" for y in per_year
            )
            divisor = 1.0 if unscaled else scale
            rows[label] = {
                y: (per_year[y].value / divisor if y in per_year else float("nan"))
                for y in years
            }
        # Preserve statement order rather than dict insertion order.
        order = [i.label for i in STATEMENT_ITEMS[self.statement]]
        frame = pd.DataFrame(rows).T
        return frame.reindex([o for o in order if o in frame.index])

    def tag_provenance(self) -> pd.DataFrame:
        """Which XBRL element supplied each value. For auditing surprises."""
        rows = [
            {"Line Item": label, "Fiscal Year": y, "XBRL Tag": fact.tag,
             "Form": fact.form, "Period End": fact.end_date}
            for label, per_year in self.facts.items()
            for y, fact in sorted(per_year.items())
        ]
        return pd.DataFrame(rows)

    def derived_ratios(self) -> dict[int, dict[str, float]]:
        """Margins and returns, computed only where both inputs exist."""
        def get(label: str, year: int) -> float | None:
            fact = self.facts.get(label, {}).get(year)
            return fact.value if fact else None

        years = sorted({y for per_year in self.facts.values() for y in per_year})
        out: dict[int, dict[str, float]] = {}
        for y in years:
            rev = get("Revenue", y)
            ratios: dict[str, float] = {}
            if rev:
                for num, name in (
                    ("Gross Profit", "Gross Margin %"),
                    ("Operating Income", "Operating Margin %"),
                    ("Net Income", "Net Margin %"),
                    ("R&D Expense", "R&D / Revenue %"),
                    ("Operating Cash Flow", "OCF / Revenue %"),
                ):
                    v = get(num, y)
                    if v is not None:
                        ratios[name] = v / rev * 100.0
            equity = get("Stockholders Equity", y)
            net = get("Net Income", y)
            if equity and net is not None and equity != 0:
                ratios["ROE %"] = net / equity * 100.0
            assets = get("Total Assets", y)
            if assets and net is not None and assets != 0:
                ratios["ROA %"] = net / assets * 100.0
            cur_a, cur_l = get("Total Current Assets", y), get("Total Current Liabilities", y)
            if cur_a and cur_l:
                ratios["Current Ratio"] = cur_a / cur_l
            debt = get("Long-term Debt", y)
            if debt is not None and equity:
                ratios["Debt / Equity"] = debt / equity
            ocf, capex = get("Operating Cash Flow", y), get("Capital Expenditure", y)
            if ocf is not None and capex is not None:
                ratios["Free Cash Flow"] = ocf + capex  # capex is already negated
            if ratios:
                out[y] = ratios
        return out


class SecAggregator:
    """Reads and normalises XBRL company facts."""

    def __init__(self, client: SecClient | None = None) -> None:
        self.client = client or SecClient()
        self._roster: dict[str, CompanyRef] | None = None

    @property
    def roster(self) -> dict[str, CompanyRef]:
        if self._roster is None:
            self._roster = load_company_tickers(self.client)
        return self._roster

    def resolve(self, ticker: str) -> CompanyRef:
        t = ticker.strip().upper()
        # SEC uses dashes where market data vendors often use dots (BRK.B).
        for candidate in (t, t.replace(".", "-"), t.replace("-", ".")):
            if candidate in self.roster:
                return self.roster[candidate]
        raise KeyError(
            f"Ticker {ticker!r} non trovato nell'indice SEC. "
            "Verifica il simbolo, o potrebbe non essere un filer SEC."
        )

    def statement(
        self,
        ticker: str,
        statement: Statement = Statement.INCOME,
        *,
        years: int = 5,
        annual_only: bool = True,
    ) -> StatementData:
        """Build one normalised statement for `ticker`."""
        ref = self.resolve(ticker)
        try:
            payload = self.client.company_facts(ref.cik)
        except Exception as exc:
            raise RuntimeError(
                f"Impossibile leggere i companyfacts di {ref.ticker} "
                f"(CIK {ref.cik}): {exc}"
            ) from exc

        gaap = payload.get("facts", {}).get("us-gaap", {})
        dei = payload.get("facts", {}).get("dei", {})
        if not gaap:
            return StatementData(
                ticker=ref.ticker, company_name=ref.name, statement=statement,
                warnings=[
                    f"{ref.ticker}: nessun fatto us-gaap. Filer estero "
                    "(20-F/IFRS) o azienda senza XBRL storico."
                ],
            )

        data = StatementData(
            ticker=ref.ticker, company_name=ref.name or ticker, statement=statement
        )
        for item in STATEMENT_ITEMS[statement]:
            resolved = self._resolve_item(item, gaap, dei, years, annual_only)
            if resolved:
                data.facts[item.label] = resolved
            else:
                data.missing_items.append(item.label)

        if data.missing_items:
            data.warnings.append(
                f"Voci non mappate per {ref.ticker}: "
                f"{', '.join(data.missing_items)}. "
                "Possono mancare dal bilancio o usare tag non in catalogo."
            )
        return data

    def _resolve_item(
        self,
        item: LineItem,
        gaap: dict,
        dei: dict,
        years: int,
        annual_only: bool,
    ) -> dict[int, FinancialFact]:
        """Walk the fallback chain until a tag yields data."""
        for tag in item.tags:
            block = gaap.get(tag) or dei.get(tag)
            if not block:
                continue
            units = block.get("units", {})
            series = units.get(item.unit)
            if series is None:
                # Take any unit rather than dropping the line; note the swap.
                if not units:
                    continue
                series = next(iter(units.values()))
            picked = self._select_periods(series, item, tag, years, annual_only)
            if picked:
                return picked
        return {}

    def _select_periods(
        self,
        series: list[dict],
        item: LineItem,
        tag: str,
        years: int,
        annual_only: bool,
    ) -> dict[int, FinancialFact]:
        """Choose one fact per fiscal year, preferring 10-K annual figures.

        Duration facts are length-filtered: without this a Q4 revenue number
        can win the FY slot and understate the year by ~75%.
        """
        by_year: dict[int, FinancialFact] = {}
        for row in series:
            fy = row.get("fy")
            fp = row.get("fp")
            if fy is None or fp is None:
                continue
            if annual_only and fp != "FY":
                continue

            if item.kind is FactKind.DURATION:
                start, end = row.get("start"), row.get("end")
                if not start or not end:
                    continue
                span = (pd.Timestamp(end) - pd.Timestamp(start)).days
                # An annual duration must actually span roughly a year.
                if annual_only and not (300 <= span <= 430):
                    continue

            val = row.get("val")
            if val is None:
                continue
            fact = FinancialFact(
                value=float(-val if item.negate else val),
                tag=tag,
                fiscal_year=int(fy),
                fiscal_period=str(fp),
                end_date=str(row.get("end", "")),
                form=str(row.get("form", "")),
                unit=item.unit,
            )
            prior = by_year.get(int(fy))
            # Prefer a 10-K over a 10-Q restatement, then the later filing.
            if prior is None or (
                (fact.form == "10-K" and prior.form != "10-K")
                or (fact.form == prior.form and fact.end_date > prior.end_date)
            ):
                by_year[int(fy)] = fact

        if not by_year:
            return {}
        keep = sorted(by_year)[-years:]
        return {y: by_year[y] for y in keep}

    def compare(
        self,
        tickers: list[str],
        statement: Statement = Statement.INCOME,
        *,
        fiscal_year: int | None = None,
        scale: float = 1e6,
    ) -> pd.DataFrame:
        """Side-by-side comparison for one fiscal year across tickers.

        Defaults to the latest year every ticker has in common, so the table
        never silently compares FY2025 against FY2023.
        """
        loaded: dict[str, StatementData] = {}
        errors: dict[str, str] = {}
        for t in tickers:
            try:
                loaded[t.upper()] = self.statement(t, statement)
            except (KeyError, RuntimeError) as exc:
                errors[t.upper()] = str(exc)

        if not loaded:
            raise RuntimeError(
                "Nessun ticker risolto. Errori: "
                + "; ".join(f"{k}: {v}" for k, v in errors.items())
            )

        year_sets = [
            {y for per_year in d.facts.values() for y in per_year}
            for d in loaded.values() if d.facts
        ]
        if not year_sets:
            raise RuntimeError("Nessun dato di bilancio recuperato.")

        if fiscal_year is None:
            common = set.intersection(*year_sets) if year_sets else set()
            fiscal_year = max(common) if common else max(set.union(*year_sets))

        order = [i.label for i in STATEMENT_ITEMS[statement]]
        cols: dict[str, dict[str, float]] = {}
        for ticker, d in loaded.items():
            col: dict[str, float] = {}
            for item in STATEMENT_ITEMS[statement]:
                fact = d.facts.get(item.label, {}).get(fiscal_year)
                if fact is None:
                    col[item.label] = float("nan")
                else:
                    divisor = 1.0 if item.unit != "USD" else scale
                    col[item.label] = fact.value / divisor
            cols[ticker] = col

        frame = pd.DataFrame(cols).reindex(order)
        frame.attrs["fiscal_year"] = fiscal_year
        frame.attrs["errors"] = errors
        frame.attrs["scale"] = scale
        return frame

    def compare_ratios(
        self, tickers: list[str], *, fiscal_year: int | None = None
    ) -> pd.DataFrame:
        """Cross-statement ratio comparison.

        Ratios need lines from all three statements (ROE wants equity, FCF
        wants capex), so this merges them before computing.
        """
        merged: dict[str, dict[str, float]] = {}
        for t in tickers:
            tu = t.upper()
            combined: dict[str, dict[int, FinancialFact]] = {}
            name = tu
            for stmt in Statement:
                try:
                    d = self.statement(t, stmt)
                except (KeyError, RuntimeError) as exc:
                    log.info("%s %s non disponibile: %s", tu, stmt.value, exc)
                    continue
                name = d.company_name or tu
                combined.update(d.facts)
            if not combined:
                continue
            holder = StatementData(
                ticker=tu, company_name=name, statement=Statement.INCOME,
                facts=combined,
            )
            ratios = holder.derived_ratios()
            if not ratios:
                continue
            year = fiscal_year if fiscal_year in ratios else max(ratios)
            merged[tu] = ratios[year]

        if not merged:
            raise RuntimeError("Nessun rapporto calcolabile per i ticker richiesti.")
        return pd.DataFrame(merged)
