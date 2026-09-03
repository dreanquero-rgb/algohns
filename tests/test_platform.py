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


# --------------------------------------------------------------------------- M2
def test_real_money_lock(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "false")
    from algohns.config import settings as settings_mod
    settings_mod.get_settings.cache_clear()
    from algohns.modules.alpaca_execution import AlpacaExecutionEngine, RealMoneyLockError

    with pytest.raises(RealMoneyLockError):
        AlpacaExecutionEngine(api_key="x", secret_key="y")
    settings_mod.get_settings.cache_clear()


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
