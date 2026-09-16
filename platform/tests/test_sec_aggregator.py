"""Tests for XBRL normalisation.

Everything runs against synthetic `companyfacts` payloads that reproduce the
pathologies real filings exhibit: legacy tags, Q4 durations mislabelled FY,
10-Q restatements competing with 10-K figures, and per-share units that must
survive monetary scaling.
"""
from __future__ import annotations

import types

import pandas as pd
import pytest

from modules.sec_aggregator import (
    BALANCE_SHEET,
    CASH_FLOW,
    INCOME_STATEMENT,
    FactKind,
    LineItem,
    SecAggregator,
    Statement,
    StatementData,
)
from modules.sec_client import CompanyRef


def _agg(payload: dict, ticker: str = "FAKE") -> SecAggregator:
    """Aggregator wired to a fixed payload, with no network or credentials."""
    a = SecAggregator.__new__(SecAggregator)
    a._roster = {ticker: CompanyRef(cik="0000000001", ticker=ticker, name="Fake Corp")}
    a.client = types.SimpleNamespace(company_facts=lambda cik: payload)
    return a


def _facts(**tags) -> dict:
    return {"facts": {"us-gaap": tags, "dei": {}}}


def _dur(fy, val, *, form="10-K", start=None, end=None, fp="FY"):
    return {
        "fy": fy, "fp": fp, "val": val, "form": form,
        "start": start or f"{fy}-01-01", "end": end or f"{fy}-12-31",
    }


def _inst(fy, val, *, form="10-K", end=None, fp="FY"):
    return {"fy": fy, "fp": fp, "val": val, "form": form, "end": end or f"{fy}-12-31"}


class TestCatalogue:
    def test_every_item_has_tags(self):
        for group in (INCOME_STATEMENT, BALANCE_SHEET, CASH_FLOW):
            for item in group:
                assert item.tags

    def test_empty_tag_chain_rejected(self):
        with pytest.raises(ValueError, match="almeno un tag"):
            LineItem("Bad", ())

    def test_balance_sheet_items_are_instants(self):
        assert all(i.kind is FactKind.INSTANT for i in BALANCE_SHEET)

    def test_income_items_are_durations(self):
        assert all(i.kind is FactKind.DURATION for i in INCOME_STATEMENT)

    def test_labels_unique_within_statement(self):
        for group in (INCOME_STATEMENT, BALANCE_SHEET, CASH_FLOW):
            labels = [i.label for i in group]
            assert len(labels) == len(set(labels))

    def test_cost_lines_are_negated(self):
        negated = {i.label for i in CASH_FLOW if i.negate}
        assert {"Capital Expenditure", "Dividends Paid", "Share Repurchases"} <= negated


class TestTagFallback:
    def test_modern_tag_preferred(self):
        payload = _facts(
            RevenueFromContractWithCustomerExcludingAssessedTax={
                "units": {"USD": [_dur(2025, 900e6)]}},
            SalesRevenueNet={"units": {"USD": [_dur(2025, 111e6)]}},
        )
        d = _agg(payload).statement("FAKE", Statement.INCOME)
        assert d.facts["Revenue"][2025].value == pytest.approx(900e6)
        assert d.facts["Revenue"][2025].tag == \
            "RevenueFromContractWithCustomerExcludingAssessedTax"

    def test_legacy_tag_used_when_modern_absent(self):
        """A filer using only SalesRevenueNet must not read as missing data."""
        payload = _facts(SalesRevenueNet={"units": {"USD": [_dur(2025, 1200e6)]}})
        d = _agg(payload).statement("FAKE", Statement.INCOME)
        assert d.facts["Revenue"][2025].value == pytest.approx(1200e6)
        assert d.facts["Revenue"][2025].tag == "SalesRevenueNet"

    def test_unmapped_line_recorded_as_missing(self):
        payload = _facts(SalesRevenueNet={"units": {"USD": [_dur(2025, 10e6)]}})
        d = _agg(payload).statement("FAKE", Statement.INCOME)
        assert "Net Income" in d.missing_items
        assert d.warnings

    def test_provenance_records_the_winning_tag(self):
        payload = _facts(SalesRevenueNet={"units": {"USD": [_dur(2025, 1e6)]}})
        d = _agg(payload).statement("FAKE", Statement.INCOME)
        prov = d.tag_provenance()
        assert set(prov["XBRL Tag"]) == {"SalesRevenueNet"}

    def test_empty_gaap_block_warns_not_crashes(self):
        d = _agg({"facts": {"us-gaap": {}, "dei": {}}}).statement("FAKE")
        assert d.facts == {}
        assert any("us-gaap" in w for w in d.warnings)


class TestPeriodSelection:
    def test_quarter_length_duration_rejected_for_annual(self):
        """A Q4-length span tagged FY must not win the fiscal year."""
        payload = _facts(SalesRevenueNet={"units": {"USD": [
            _dur(2025, 1200e6),
            _dur(2025, 310e6, start="2025-10-01", end="2025-12-31"),
        ]}})
        d = _agg(payload).statement("FAKE", Statement.INCOME)
        assert d.facts["Revenue"][2025].value == pytest.approx(1200e6)

    def test_10k_preferred_over_10q(self):
        payload = _facts(NetIncomeLoss={"units": {"USD": [
            _dur(2025, 90e6, form="10-Q"),
            _dur(2025, 100e6, form="10-K"),
        ]}})
        d = _agg(payload).statement("FAKE", Statement.INCOME)
        assert d.facts["Net Income"][2025].form == "10-K"
        assert d.facts["Net Income"][2025].value == pytest.approx(100e6)

    def test_later_filing_wins_within_same_form(self):
        payload = _facts(NetIncomeLoss={"units": {"USD": [
            _dur(2025, 100e6, end="2025-12-31"),
            _dur(2025, 105e6, end="2025-12-30"),
        ]}})
        d = _agg(payload).statement("FAKE", Statement.INCOME)
        assert d.facts["Net Income"][2025].value == pytest.approx(100e6)

    def test_years_limit_keeps_most_recent(self):
        rows = [_dur(y, y * 1e6) for y in range(2015, 2026)]
        payload = _facts(SalesRevenueNet={"units": {"USD": rows}})
        d = _agg(payload).statement("FAKE", Statement.INCOME, years=3)
        assert sorted(d.facts["Revenue"]) == [2023, 2024, 2025]

    def test_instant_facts_need_no_duration(self):
        payload = _facts(Assets={"units": {"USD": [_inst(2025, 5000e6)]}})
        d = _agg(payload).statement("FAKE", Statement.BALANCE)
        assert d.facts["Total Assets"][2025].value == pytest.approx(5000e6)

    def test_quarterly_excluded_by_default(self):
        payload = _facts(NetIncomeLoss={"units": {"USD": [
            _dur(2025, 25e6, fp="Q1", start="2025-01-01", end="2025-03-31"),
        ]}})
        d = _agg(payload).statement("FAKE", Statement.INCOME)
        assert "Net Income" in d.missing_items

    def test_rows_without_fiscal_year_skipped(self):
        payload = _facts(NetIncomeLoss={"units": {"USD": [
            {"fp": "FY", "val": 1e6, "start": "2025-01-01", "end": "2025-12-31"},
            _dur(2025, 50e6),
        ]}})
        d = _agg(payload).statement("FAKE", Statement.INCOME)
        assert d.facts["Net Income"][2025].value == pytest.approx(50e6)


class TestSigns:
    def test_capex_negated(self):
        payload = _facts(PaymentsToAcquirePropertyPlantAndEquipment={
            "units": {"USD": [_dur(2025, 80e6)]}})
        d = _agg(payload).statement("FAKE", Statement.CASHFLOW)
        assert d.facts["Capital Expenditure"][2025].value == pytest.approx(-80e6)

    def test_operating_cash_flow_not_negated(self):
        payload = _facts(NetCashProvidedByUsedInOperatingActivities={
            "units": {"USD": [_dur(2025, 300e6)]}})
        d = _agg(payload).statement("FAKE", Statement.CASHFLOW)
        assert d.facts["Operating Cash Flow"][2025].value == pytest.approx(300e6)


class TestScaling:
    def test_monetary_lines_scaled(self):
        payload = _facts(SalesRevenueNet={"units": {"USD": [_dur(2025, 1200e6)]}})
        frame = _agg(payload).statement("FAKE", Statement.INCOME).to_frame(scale=1e6)
        assert frame.loc["Revenue", 2025] == pytest.approx(1200.0)

    def test_per_share_lines_never_scaled(self):
        """Dividing EPS by a million is how a table becomes a column of zeros."""
        payload = _facts(
            SalesRevenueNet={"units": {"USD": [_dur(2025, 1200e6)]}},
            EarningsPerShareDiluted={"units": {"USD/shares": [_dur(2025, 3.75)]}},
        )
        frame = _agg(payload).statement("FAKE", Statement.INCOME).to_frame(scale=1e6)
        assert frame.loc["EPS Diluted", 2025] == pytest.approx(3.75)

    def test_frame_follows_statement_order(self):
        payload = _facts(
            NetIncomeLoss={"units": {"USD": [_dur(2025, 100e6)]}},
            SalesRevenueNet={"units": {"USD": [_dur(2025, 1200e6)]}},
        )
        frame = _agg(payload).statement("FAKE", Statement.INCOME).to_frame()
        assert list(frame.index) == ["Revenue", "Net Income"]

    def test_empty_facts_give_empty_frame(self):
        d = StatementData("X", "X", Statement.INCOME)
        assert d.to_frame().empty


class TestDerivedRatios:
    @pytest.fixture
    def combined(self) -> StatementData:
        payload = _facts(
            SalesRevenueNet={"units": {"USD": [_dur(2025, 1200e6)]}},
            NetIncomeLoss={"units": {"USD": [_dur(2025, 150e6)]}},
            GrossProfit={"units": {"USD": [_dur(2025, 600e6)]}},
            OperatingIncomeLoss={"units": {"USD": [_dur(2025, 240e6)]}},
            Assets={"units": {"USD": [_inst(2025, 5000e6)]}},
            StockholdersEquity={"units": {"USD": [_inst(2025, 2000e6)]}},
            AssetsCurrent={"units": {"USD": [_inst(2025, 1500e6)]}},
            LiabilitiesCurrent={"units": {"USD": [_inst(2025, 1000e6)]}},
            LongTermDebtNoncurrent={"units": {"USD": [_inst(2025, 800e6)]}},
            NetCashProvidedByUsedInOperatingActivities={
                "units": {"USD": [_dur(2025, 300e6)]}},
            PaymentsToAcquirePropertyPlantAndEquipment={
                "units": {"USD": [_dur(2025, 80e6)]}},
        )
        a = _agg(payload)
        merged: dict = {}
        for stmt in Statement:
            merged.update(a.statement("FAKE", stmt).facts)
        return StatementData("FAKE", "Fake Corp", Statement.INCOME, facts=merged)

    def test_margins(self, combined):
        r = combined.derived_ratios()[2025]
        assert r["Gross Margin %"] == pytest.approx(50.0)
        assert r["Operating Margin %"] == pytest.approx(20.0)
        assert r["Net Margin %"] == pytest.approx(12.5)

    def test_returns(self, combined):
        r = combined.derived_ratios()[2025]
        assert r["ROE %"] == pytest.approx(7.5)
        assert r["ROA %"] == pytest.approx(3.0)

    def test_leverage_and_liquidity(self, combined):
        r = combined.derived_ratios()[2025]
        assert r["Current Ratio"] == pytest.approx(1.5)
        assert r["Debt / Equity"] == pytest.approx(0.4)

    def test_free_cash_flow_uses_negated_capex(self, combined):
        assert combined.derived_ratios()[2025]["Free Cash Flow"] == pytest.approx(220e6)

    def test_no_ratios_without_revenue(self):
        payload = _facts(NetIncomeLoss={"units": {"USD": [_dur(2025, 100e6)]}})
        d = _agg(payload).statement("FAKE", Statement.INCOME)
        assert "Net Margin %" not in d.derived_ratios().get(2025, {})

    def test_zero_equity_does_not_divide(self):
        payload = _facts(
            SalesRevenueNet={"units": {"USD": [_dur(2025, 100e6)]}},
            NetIncomeLoss={"units": {"USD": [_dur(2025, 10e6)]}},
            StockholdersEquity={"units": {"USD": [_inst(2025, 0.0)]}},
        )
        a = _agg(payload)
        merged: dict = {}
        for stmt in Statement:
            merged.update(a.statement("FAKE", stmt).facts)
        holder = StatementData("F", "F", Statement.INCOME, facts=merged)
        assert "ROE %" not in holder.derived_ratios()[2025]


class TestComparison:
    def test_common_year_chosen(self):
        """Never compare one filer's FY2025 against another's FY2023."""
        a = SecAggregator.__new__(SecAggregator)
        a._roster = {
            "AAA": CompanyRef(cik="1", ticker="AAA", name="A Corp"),
            "BBB": CompanyRef(cik="2", ticker="BBB", name="B Corp"),
        }
        payloads = {
            "1": _facts(SalesRevenueNet={"units": {"USD": [
                _dur(2024, 100e6), _dur(2025, 110e6)]}}),
            "2": _facts(SalesRevenueNet={"units": {"USD": [
                _dur(2023, 200e6), _dur(2024, 220e6)]}}),
        }
        a.client = types.SimpleNamespace(company_facts=lambda cik: payloads[cik])
        frame = a.compare(["AAA", "BBB"], Statement.INCOME)
        # 2024 is the only year both report.
        assert frame.attrs["fiscal_year"] == 2024
        assert frame.loc["Revenue", "AAA"] == pytest.approx(100.0)
        assert frame.loc["Revenue", "BBB"] == pytest.approx(220.0)

    def test_unresolvable_ticker_recorded_not_fatal(self):
        payload = _facts(SalesRevenueNet={"units": {"USD": [_dur(2025, 100e6)]}})
        frame = _agg(payload).compare(["FAKE", "NOSUCH"], Statement.INCOME)
        assert "NOSUCH" in frame.attrs["errors"]
        assert "FAKE" in frame.columns

    def test_all_tickers_failing_raises(self):
        payload = _facts(SalesRevenueNet={"units": {"USD": [_dur(2025, 1e6)]}})
        with pytest.raises(RuntimeError, match="Nessun ticker risolto"):
            _agg(payload).compare(["NOPE1", "NOPE2"])

    def test_missing_line_becomes_nan_not_zero(self):
        payload = _facts(SalesRevenueNet={"units": {"USD": [_dur(2025, 100e6)]}})
        frame = _agg(payload).compare(["FAKE"], Statement.INCOME)
        assert pd.isna(frame.loc["Net Income", "FAKE"])


class TestTickerResolution:
    def test_dot_and_dash_variants_resolve(self):
        a = SecAggregator.__new__(SecAggregator)
        a._roster = {"BRK-B": CompanyRef(cik="1", ticker="BRK-B", name="Berkshire")}
        assert a.resolve("BRK.B").ticker == "BRK-B"
        assert a.resolve("brk-b").ticker == "BRK-B"

    def test_unknown_ticker_raises_with_guidance(self):
        a = SecAggregator.__new__(SecAggregator)
        a._roster = {}
        with pytest.raises(KeyError, match="non trovato nell'indice SEC"):
            a.resolve("ZZZZ")
