"""Smoke + logic tests for the pure-Python cores of Algohns V12.

Run with:  pytest -q
These tests avoid any network / heavy optional dependency so they pass on a
minimal install (numpy + pandas + scipy).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest


# --------------------------------------------------------------------------- M1
def test_bond_engine_reprices_and_tax_ordering():
    from algohns.modules.bond_engine import Bond, BondEngine

    bond = Bond(
        coupon_rate=0.035, frequency=2,
        issue_date=date(2020, 1, 1), maturity_date=date(2030, 3, 1),
        settlement_date=date(2026, 9, 3), clean_price=98.4,
    )
    eng = BondEngine()
    gross = eng.analyse(bond, "GROSS")
    gov = eng.analyse(bond, "IT_GOV_WHITELIST")
    corp = eng.analyse(bond, "IT_CORPORATE")

    # Gross YTM must reprice the bond to its dirty price.
    times = np.array([r["years"] for r in gross.cashflow_table])
    cfs = np.array([r["gross_cf"] for r in gross.cashflow_table])
    pv = float(np.sum(cfs / (1 + gross.ytm_gross) ** times))
    assert abs(pv - gross.dirty_price) < 1e-2

    # Tax ordering: gross > 12.5% net > 26% net.
    assert gross.ytm_gross > gov.ytm_net > corp.ytm_net > 0
    assert 0 < gross.modified_duration < gross.macaulay_duration
    assert gross.convexity > 0


# --------------------------------------------------------------------------- M3
def _synthetic_prices():
    np.random.seed(7)
    idx = pd.bdate_range("2019-01-01", periods=252 * 3)
    data = {c: 100 * np.cumprod(1 + np.random.normal(5e-4, 0.012, len(idx)))
            for c in ("AAA", "BBB", "CCC")}
    return pd.DataFrame(data, index=idx)


@pytest.mark.parametrize("method", ["max_sharpe", "min_volatility", "risk_parity", "equal_weight"])
def test_optimizer_weights_sum_to_one(method):
    from algohns.modules.backtest_suite import PortfolioOptimizer

    w = PortfolioOptimizer(_synthetic_prices()).optimize(method)
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert all(v >= 0 for v in w.values())


def test_metrics_and_backtest():
    from algohns.modules.backtest_suite import Backtester, PortfolioOptimizer, compute_metrics

    prices = _synthetic_prices()
    m = compute_metrics(prices["AAA"].pct_change().dropna())
    assert -1 < m.max_drawdown <= 0 and m.annual_volatility > 0
    assert not np.isnan(m.sharpe)

    w = PortfolioOptimizer(prices).optimize("max_sharpe")
    res = Backtester(prices).run(w, rebalance="Q")
    assert len(res.equity_curve) > 0
    assert res.summary()["final_equity"] > 0


# ----------------------------------------------------------------- M1 screener
def test_bond_screener_sample_universe():
    from algohns.modules.bond_data import BondScreener

    sc = BondScreener()
    bonds, source = sc.load_universe()          # network blocked in CI -> sample
    assert bonds and source in ("live", "sample")
    df = sc.build_table(bonds, tax_key="IT_GOV_WHITELIST")
    assert not df.empty and {"ISIN", "NetYTM%", "YTM%", "ModDur"} <= set(df.columns)
    taxed = df.dropna(subset=["YTM%", "NetYTM%"])
    assert len(taxed) >= 5
    assert (taxed["NetYTM%"] <= taxed["YTM%"] + 1e-9).all()   # net never exceeds gross


# --------------------------------------------------------------------------- M2
def test_real_money_lock(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "false")
    from algohns.config import settings as settings_mod
    settings_mod.get_settings.cache_clear()
    from algohns.modules.alpaca_execution import AlpacaExecutionEngine, RealMoneyLockError

    with pytest.raises(RealMoneyLockError):
        AlpacaExecutionEngine(api_key="x", secret_key="y")
    settings_mod.get_settings.cache_clear()


# ------------------------------------------------------------ M3 universe
def test_universe_search():
    from algohns.modules import universe

    if not universe.available():
        import pytest as _pt
        _pt.skip("financedatabase not installed")
    df = universe.search("Equities", filters={"country": "Italy"}, query="", limit=25)
    assert not df.empty and "symbol" in df.columns
    assert universe.tickers_from(df)


# ------------------------------------------------------------- M2 risk profile
def test_risk_profile_scoring_and_allocation():
    from algohns.modules.risk_profile import QUESTIONS, compute_profile

    cons = compute_profile({q.key: 0 for q in QUESTIONS})
    aggr = compute_profile({q.key: 4 for q in QUESTIONS})
    assert cons.label == "Conservative" and aggr.label == "Aggressive"
    assert cons.score == 0.0 and aggr.score == 100.0
    for p in (cons, aggr):
        assert abs(sum(p.ticker_allocation.values()) - 1.0) < 1e-6
    tilt = compute_profile({q.key: 2 for q in QUESTIONS}, preferences=["gold"])
    assert "GLD" in tilt.ticker_allocation
    assert abs(sum(tilt.ticker_allocation.values()) - 1.0) < 1e-6


# --------------------------------------------------------------------------- M4
def test_supply_chain_regex_extraction():
    from algohns.modules.supply_chain_graph import SupplyChainAnalyzer

    text = ("Our largest customers include Verizon Communications Inc and AT&T Inc. "
            "We rely on Taiwan Semiconductor Manufacturing Company as a supplier.")
    rels = SupplyChainAnalyzer().extract_relationships("AAPL", text)
    relations = {r.relation for r in rels}
    assert "customer" in relations and "supplier" in relations
    assert any("Verizon" in r.target for r in rels)


# --------------------------------------------------------------------------- M5
def test_sec_statement_and_ratios():
    from algohns.modules.sec_aggregator import CompanyFacts, SECAggregator

    facts = CompanyFacts("XYZ", "0000000001", "XYZ Corp", raw={"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [{"form": "10-K", "fp": "FY", "fy": 2024, "end": "2024-12-31", "val": 500e9}]}},
        "NetIncomeLoss": {"units": {"USD": [{"form": "10-K", "fp": "FY", "fy": 2024, "end": "2024-12-31", "val": 90e9}]}},
        "StockholdersEquity": {"units": {"USD": [{"form": "10-K", "fp": "FY", "fy": 2024, "end": "2024-12-31", "val": 150e9}]}},
    }}})
    agg = SECAggregator()
    inc = agg.statement(facts, "income_statement")
    assert inc["Revenue"] == 500e9 and inc["Net Income"] == 90e9
    ratios = agg.key_ratios(facts)
    assert ratios["Net Margin"] == round(90e9 / 500e9, 4)
    assert ratios["ROE"] == round(90e9 / 150e9, 4)


def test_sec_full_statements_and_kpis():
    from algohns.modules.sec_aggregator import SECAggregator, sample_facts

    agg = SECAggregator()
    facts = sample_facts("AAPL")
    inc = agg.full_statement(facts, "income_statement", years=5)
    assert inc.shape[1] == 5 and inc.shape[0] >= 10          # multi-year, full lines
    kpis = agg.kpis(facts)
    # Fixture is in absolute USD, matching the scale of real SEC XBRL facts.
    assert kpis["Revenue"]["value"] == 391_035 * 1_000_000
    assert kpis["Revenue"]["yoy"] is not None                # YoY computed
    assert kpis["EPS (Diluted)"]["value"] == 6.08            # per-share stays unscaled

    # "Bilanci interi": every line of every statement must be populated.
    for stmt in ("income_statement", "balance_sheet", "cash_flow"):
        df = agg.full_statement(facts, stmt, years=5)
        assert df.isna().all(axis=1).sum() == 0, f"{stmt} has empty lines"


# ------------------------------------------------------- charts & real data
def test_chart_builders_produce_valid_figures():
    """Every chart builder must serialise (catches Plotly schema errors)."""
    import numpy as np
    from algohns import charts as ch

    idx = pd.bdate_range("2023-01-01", periods=120)
    df = pd.DataFrame({"A": np.linspace(100, 130, 120), "B": np.linspace(100, 115, 120)}, index=idx)
    figs = [
        ch.line(df, "t"),
        ch.area((df["A"] / df["A"].cummax() - 1).rename("dd"), negative=True),
        ch.hbar(["x", "y"], [1.5, 2.5], suffix="%"),
        ch.bar(["a", "b"], [1, -2], color_by_sign=True),
        ch.grouped_bar(pd.DataFrame({"P": [1, 2], "Q": [3, 4]}, index=["r1", "r2"])),
        ch.stacked_bar(pd.DataFrame({"P": [1, 2], "Q": [3, 4]}, index=["r1", "r2"])),
        ch.scatter(pd.DataFrame({"x": [1, 2], "y": [3, 4], "n": ["a", "b"], "g": ["IT", "DE"]}),
                   "x", "y", "n", group="g"),
        ch.heatmap(df.corr()),
        ch.donut(["a", "b"], [0.6, 0.4]),
        ch.waterfall(["r", "c", "n"], [10, -4, None], ["absolute", "relative", "total"]),
    ]
    for f in figs:
        assert f.to_json()          # full Plotly validation
    # palette integrity: fixed order, never cycled within the 8 slots
    assert len(ch.SERIES) == 8 and len(set(ch.SERIES)) == 8
    assert ch.color(0) == ch.SERIES[0]


def test_bundled_reference_data_is_real():
    from algohns.modules import reference_data as rd

    const = rd.sp500_constituents()
    assert len(const) > 400 and {"Symbol", "GICS Sector", "CIK"} <= set(const.columns)
    # real, verifiable CIKs
    assert rd.cik_map()["AAPL"] == "0000320193"
    assert rd.cik_map()["MSFT"] == "0000789019"

    hist = rd.spx_history()
    assert len(hist) > 1500 and hist.index.min().year <= 1875
    assert (hist["SP500"] > 0).all()

    y10 = rd.us10y()
    assert len(y10) > 700 and y10.index.min().year <= 1953

    assert len(rd.spx_prices("1990-01-01")) > 300


# ---------------------------------------------------- crash regressions (live)
def test_screener_columns_exist_even_with_unpriced_bonds():
    """Regression: live MOT rows without prices produced no NetYTM% column,
    so the page's sort_values('NetYTM%') raised KeyError."""
    from datetime import date as _d
    from algohns.modules.bond_data import BondScreener, ScreenerBond

    unpriced = [ScreenerBond(isin="IT0000000001", name="BTP no price", market="BTP",
                             country="IT", type="govt", price=None, coupon=None,
                             maturity=_d(2030, 1, 1))]
    df = BondScreener().build_table(unpriced)
    for col in ("YTM%", "NetYTM%", "ModDur", "Curr.Yield%", "Accrued"):
        assert col in df.columns, f"{col} must exist even when nothing computes"
    df.sort_values("NetYTM%", na_position="last")      # must not raise


def test_backtester_gives_clear_error_on_unusable_data():
    """Regression: empty or too-short price data surfaced as pandas'
    'No objects to concatenate' instead of an actionable message."""
    from algohns.modules.backtest_suite import Backtester

    empty = pd.DataFrame({"AAA": [], "BBB": []}, dtype=float)
    with pytest.raises(ValueError) as e:
        Backtester(empty).run({"AAA": 0.5, "BBB": 0.5})
    assert "concatenate" not in str(e.value).lower()

    one_row = pd.DataFrame({"AAA": [100.0], "BBB": [100.0]},
                           index=pd.to_datetime(["2024-01-02"]))
    with pytest.raises(ValueError) as e2:
        Backtester(one_row).run({"AAA": 0.5, "BBB": 0.5})
    assert "history" in str(e2.value).lower()


def test_supply_chain_map_is_rich():
    """The curated map must be substantial, not a toy example."""
    from algohns.modules.supply_chain_graph import SupplyChainAnalyzer, sample_results

    res = sample_results()
    an = SupplyChainAnalyzer()
    g = an.build_graph(res)
    assert len(res) >= 30, "at least 30 focal companies"
    assert g.number_of_edges() >= 150, "at least 150 supply-chain links"
    m = an.systemic_metrics(g)
    assert m["top_systemic"] and m["nodes"] > 100
