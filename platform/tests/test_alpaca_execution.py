"""Tests for order validation, the paper lock and rebalance arithmetic.

All of this runs without network or credentials: the connector is exercised
through a stub snapshot, so `build_rebalance` is tested as pure logic.
"""
from __future__ import annotations

import pytest

from modules.alpaca_execution import (
    AlpacaConnector,
    LiveTradingBlocked,
    OrderIntent,
    OrderSide,
    OrderType,
    PortfolioSnapshot,
    Position,
    TimeInForce,
    require_paper,
)


class TestPaperLock:
    def test_paper_host_allowed(self):
        assert require_paper("https://paper-api.alpaca.markets")

    @pytest.mark.parametrize("url", [
        "https://api.alpaca.markets",                          # the real live host
        "https://evil.example/paper-api.alpaca.markets",        # path spoof
        "http://paper-api.alpaca.markets.attacker.io",          # suffix spoof
        "https://broker-api.alpaca.markets",
        "",
        "not-a-url",
    ])
    def test_non_paper_blocked(self, url):
        with pytest.raises(LiveTradingBlocked):
            require_paper(url)

    def test_connector_refuses_live_url(self):
        with pytest.raises(LiveTradingBlocked):
            AlpacaConnector("k", "s", base_url="https://api.alpaca.markets")


class TestOrderIntent:
    def test_notional_order_valid(self):
        o = OrderIntent(symbol="AAPL", side=OrderSide.BUY, notional=1000)
        assert o.notional == 1000 and o.qty is None

    def test_requires_exactly_one_sizing(self):
        with pytest.raises(ValueError, match="exactly one"):
            OrderIntent(symbol="AAPL", side=OrderSide.BUY)
        with pytest.raises(ValueError, match="exactly one"):
            OrderIntent(symbol="AAPL", side=OrderSide.BUY, qty=1, notional=100)

    @pytest.mark.parametrize("qty", [0, -1, -0.5])
    def test_rejects_nonpositive_qty(self, qty):
        with pytest.raises(ValueError, match="qty must be positive"):
            OrderIntent(symbol="AAPL", side=OrderSide.BUY, qty=qty)

    def test_rejects_blank_symbol(self):
        with pytest.raises(ValueError, match="symbol is required"):
            OrderIntent(symbol="  ", side=OrderSide.BUY, qty=1)

    def test_limit_requires_price(self):
        with pytest.raises(ValueError, match="limit_price"):
            OrderIntent(symbol="A", side=OrderSide.BUY, qty=1,
                        order_type=OrderType.LIMIT)

    def test_stop_requires_price(self):
        with pytest.raises(ValueError, match="stop_price"):
            OrderIntent(symbol="A", side=OrderSide.BUY, qty=1,
                        order_type=OrderType.STOP)

    def test_stop_limit_requires_both(self):
        with pytest.raises(ValueError):
            OrderIntent(symbol="A", side=OrderSide.BUY, qty=1,
                        order_type=OrderType.STOP_LIMIT, limit_price=10)

    def test_fractional_allowed_for_market_day(self):
        o = OrderIntent(symbol="A", side=OrderSide.BUY, qty=0.5)
        assert o.qty == 0.5

    def test_fractional_rejected_for_limit(self):
        with pytest.raises(ValueError, match="frazionarie"):
            OrderIntent(symbol="A", side=OrderSide.BUY, qty=0.5,
                        order_type=OrderType.LIMIT, limit_price=10)

    def test_fractional_rejected_for_gtc(self):
        with pytest.raises(ValueError, match="frazionarie"):
            OrderIntent(symbol="A", side=OrderSide.BUY, qty=0.5,
                        time_in_force=TimeInForce.GTC)


def _snapshot(equity=100_000.0, holdings=None) -> PortfolioSnapshot:
    holdings = holdings or {}
    positions = [
        Position(symbol=s, qty=v / 100.0, avg_entry_price=100.0, market_value=v,
                 unrealized_pl=0.0, unrealized_plpc=0.0, current_price=100.0)
        for s, v in holdings.items()
    ]
    return PortfolioSnapshot(
        equity=equity, cash=equity - sum(holdings.values()),
        buying_power=equity, positions=positions,
    )


class _StubConnector(AlpacaConnector):
    """Bypasses __init__ so no credentials or network are needed."""

    def __init__(self):  # noqa: D107
        self.base_url = "https://paper-api.alpaca.markets"


class TestSnapshot:
    def test_weights(self):
        s = _snapshot(100_000, {"AAPL": 25_000, "MSFT": 15_000})
        assert s.weights == pytest.approx({"AAPL": 0.25, "MSFT": 0.15})
        assert s.invested_value == 40_000

    def test_concentration_single_name(self):
        assert _snapshot(100_000, {"AAPL": 100_000}).concentration == pytest.approx(1.0)

    def test_concentration_diversified_is_lower(self):
        four = _snapshot(100_000, {c: 25_000 for c in "ABCD"})
        assert four.concentration == pytest.approx(0.25)

    def test_zero_equity_yields_no_weights(self):
        assert _snapshot(0.0, {}).weights == {}


class TestRebalance:
    def test_buys_into_empty_portfolio(self):
        plan = _StubConnector().build_rebalance(
            {"AAPL": 0.5, "MSFT": 0.5}, snapshot=_snapshot(100_000, {})
        )
        assert {i.symbol for i in plan.intents} == {"AAPL", "MSFT"}
        assert all(i.side is OrderSide.BUY for i in plan.intents)
        assert all(i.notional == pytest.approx(50_000) for i in plan.intents)
        assert plan.turnover == pytest.approx(1.0)

    def test_liquidates_unlisted_position_by_qty(self):
        """Closing to zero must use share qty, not notional, to avoid dust."""
        plan = _StubConnector().build_rebalance(
            {"AAPL": 1.0}, snapshot=_snapshot(100_000, {"AAPL": 50_000, "OLD": 50_000})
        )
        sell = next(i for i in plan.intents if i.symbol == "OLD")
        assert sell.side is OrderSide.SELL
        assert sell.qty == pytest.approx(500.0)   # 50_000 / 100.0
        assert sell.notional is None

    def test_keeps_position_when_liquidate_disabled(self):
        plan = _StubConnector().build_rebalance(
            {"AAPL": 0.5}, snapshot=_snapshot(100_000, {"AAPL": 50_000, "OLD": 50_000}),
            liquidate_unlisted=False,
        )
        assert "OLD" not in {i.symbol for i in plan.intents}

    def test_skips_small_drift(self):
        plan = _StubConnector().build_rebalance(
            {"AAPL": 0.502}, snapshot=_snapshot(100_000, {"AAPL": 50_000}),
            min_drift=0.005,
        )
        assert plan.is_empty
        assert "AAPL" in plan.skipped

    def test_trades_when_drift_exceeds_threshold(self):
        plan = _StubConnector().build_rebalance(
            {"AAPL": 0.60}, snapshot=_snapshot(100_000, {"AAPL": 50_000}),
        )
        buy = next(i for i in plan.intents if i.symbol == "AAPL")
        assert buy.side is OrderSide.BUY
        assert buy.notional == pytest.approx(10_000)

    def test_rejects_weights_over_one(self):
        with pytest.raises(ValueError, match="sommano"):
            _StubConnector().build_rebalance(
                {"A": 0.7, "B": 0.7}, snapshot=_snapshot()
            )

    def test_rejects_negative_weights(self):
        with pytest.raises(ValueError, match="negativi"):
            _StubConnector().build_rebalance({"A": -0.2}, snapshot=_snapshot())

    def test_rejects_nonpositive_equity(self):
        with pytest.raises(ValueError, match="equity"):
            _StubConnector().build_rebalance({"A": 1.0}, snapshot=_snapshot(0.0))

    def test_partial_cash_allocation_allowed(self):
        """Weights summing under 1.0 leave the remainder in cash."""
        plan = _StubConnector().build_rebalance(
            {"AAPL": 0.3}, snapshot=_snapshot(100_000, {})
        )
        assert plan.intents[0].notional == pytest.approx(30_000)

    def test_drifts_reported_for_every_name(self):
        plan = _StubConnector().build_rebalance(
            {"AAPL": 0.5, "NEW": 0.5}, snapshot=_snapshot(100_000, {"AAPL": 100_000})
        )
        assert set(plan.drifts) == {"AAPL", "NEW"}
        assert plan.drifts["AAPL"] == pytest.approx(-0.5)
        assert plan.drifts["NEW"] == pytest.approx(0.5)


class TestBatchOrdering:
    def test_sells_precede_buys(self):
        """Proceeds from sells must be available before the buy leg."""
        conn = _StubConnector()
        intents = [
            OrderIntent(symbol="BUY1", side=OrderSide.BUY, notional=100),
            OrderIntent(symbol="SELL1", side=OrderSide.SELL, qty=1),
            OrderIntent(symbol="BUY2", side=OrderSide.BUY, notional=100),
            OrderIntent(symbol="SELL2", side=OrderSide.SELL, qty=1),
        ]
        results = conn.submit_all(intents, dry_run=True)
        sides = [r.intent.side for r in results]
        assert sides[:2] == [OrderSide.SELL, OrderSide.SELL]
        assert sides[2:] == [OrderSide.BUY, OrderSide.BUY]
        assert all(r.accepted and r.status == "dry_run" for r in results)
