"""Tests for the paper-account rebalance planner.

This is the path behind the "Apply profile → Send orders" button. The bug it
guards against is real: a dry-run must send nothing, a real run must actually
submit every planned order, and a full rebalance must also exit positions that
are not in the target. No Alpaca and no network here — the snapshot and the
submit call are stubbed on the instance, so only the planning logic is exercised.
"""
from __future__ import annotations

import pytest

from algohns.modules.alpaca_execution import AlpacaExecutionEngine, OrderTicket


def _engine(snapshot):
    """An engine whose snapshot is fixed and whose submits are recorded."""
    eng = AlpacaExecutionEngine(api_key="k", secret_key="s")
    eng._snap = snapshot
    eng.submitted: list[OrderTicket] = []
    eng.portfolio_snapshot = lambda: eng._snap  # type: ignore[method-assign]

    def _submit(ticket: OrderTicket):
        eng.submitted.append(ticket)
        return {"id": f"oid-{len(eng.submitted)}", "status": "accepted",
                "symbol": ticket.symbol}

    eng.submit_order = _submit  # type: ignore[method-assign]
    return eng


def _snap(equity=10_000.0, positions=None):
    return {"equity": equity, "cash": equity, "buying_power": equity,
            "positions": positions or []}


class TestRebalancePlanner:
    def test_dry_run_sends_nothing(self):
        eng = _engine(_snap())
        plan = eng.rebalance_to_weights({"SPY": 0.6, "AGG": 0.4}, dry_run=True)
        assert len(plan) == 2
        assert eng.submitted == []
        assert all("result" not in p for p in plan)

    def test_execute_submits_every_order(self):
        eng = _engine(_snap())
        plan = eng.rebalance_to_weights({"SPY": 0.6, "AGG": 0.4}, dry_run=False)
        assert len(eng.submitted) == 2
        assert {t.symbol for t in eng.submitted} == {"SPY", "AGG"}
        assert all(isinstance(p.get("result"), dict) for p in plan)
        assert all(p["result"]["status"] == "accepted" for p in plan)

    def test_notional_matches_target_value(self):
        eng = _engine(_snap(equity=10_000.0))
        eng.rebalance_to_weights({"SPY": 0.6}, dry_run=False)
        spy = eng.submitted[0]
        assert spy.side == "buy"
        assert spy.notional == pytest.approx(6_000.0)  # 60% of 10k, flat start

    def test_below_threshold_is_skipped(self):
        # Already holding ~the target -> the tiny delta is below the min ticket.
        snap = _snap(positions=[{"symbol": "SPY", "qty": 20.0,
                                 "market_value": 6_000.0, "unrealized_pl": 0.0,
                                 "weight": 0.6}])
        eng = _engine(snap)
        plan = eng.rebalance_to_weights({"SPY": 0.6}, dry_run=False)
        assert plan == []
        assert eng.submitted == []

    def test_close_untracked_liquidates_off_target(self):
        snap = _snap(positions=[{"symbol": "TSLA", "qty": 5.0,
                                 "market_value": 4_000.0, "unrealized_pl": 0.0,
                                 "weight": 0.4}])
        eng = _engine(snap)
        plan = eng.rebalance_to_weights({"SPY": 1.0}, dry_run=False,
                                        close_untracked=True)
        symbols = {t.symbol for t in eng.submitted}
        assert {"SPY", "TSLA"} <= symbols
        tsla = next(t for t in eng.submitted if t.symbol == "TSLA")
        assert tsla.side == "sell"
        assert tsla.qty == 5.0            # full-quantity exit
        assert tsla.notional is None
        assert any(p["action"] == "exit" for p in plan)

    def test_untracked_kept_when_flag_off(self):
        snap = _snap(positions=[{"symbol": "TSLA", "qty": 5.0,
                                 "market_value": 4_000.0, "unrealized_pl": 0.0,
                                 "weight": 0.4}])
        eng = _engine(snap)
        eng.rebalance_to_weights({"SPY": 1.0}, dry_run=False,
                                 close_untracked=False)
        assert "TSLA" not in {t.symbol for t in eng.submitted}

    def test_symbols_are_upper_cased(self):
        eng = _engine(_snap())
        eng.rebalance_to_weights({"spy": 1.0}, dry_run=False)
        assert eng.submitted[0].symbol == "SPY"
