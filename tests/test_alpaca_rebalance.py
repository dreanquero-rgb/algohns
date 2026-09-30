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

    def test_one_bad_ticker_does_not_abort_the_batch(self):
        """A symbol Alpaca rejects must not stop the other orders."""
        eng = _engine(_snap())

        def _submit(ticket: OrderTicket):
            if ticket.symbol == "BADX":
                raise RuntimeError('asset "BADX" not found')
            eng.submitted.append(ticket)
            return {"id": "ok", "status": "accepted", "symbol": ticket.symbol}

        eng.submit_order = _submit  # type: ignore[method-assign]
        plan = eng.rebalance_to_weights({"SPY": 0.5, "BADX": 0.5}, dry_run=False)
        # the good order still went through
        assert {t.symbol for t in eng.submitted} == {"SPY"}
        status = {p["symbol"]: p.get("status") for p in plan}
        assert status["SPY"] == "sent"
        assert status["BADX"] == "error"
        bad = next(p for p in plan if p["symbol"] == "BADX")
        assert "not found" in bad["error"]

    def test_tradable_filter_skips_untradable_without_submitting(self):
        eng = _engine(_snap())
        plan = eng.rebalance_to_weights(
            {"SPY": 0.5, "3690N.MX": 0.5}, dry_run=False, tradable={"SPY"})
        assert {t.symbol for t in eng.submitted} == {"SPY"}  # only the tradable one
        status = {p["symbol"]: p.get("status") for p in plan}
        assert status["SPY"] == "sent"
        assert status["3690N.MX"] == "skipped"
        skipped = next(p for p in plan if p["symbol"] == "3690N.MX")
        assert "not tradable" in skipped["error"]


class _FakeConfig:
    def __init__(self, suspend_trade: bool):
        self.suspend_trade = suspend_trade

    def model_dump(self):
        return {"suspend_trade": self.suspend_trade, "no_shorting": False}


class _FakeClient:
    def __init__(self, suspend_trade: bool = True):
        self.cfg = _FakeConfig(suspend_trade)
        self.set_called_with: bool | None = None

    def get_account_configurations(self):
        return self.cfg

    def set_account_configurations(self, cfg):
        self.set_called_with = cfg.suspend_trade
        self.cfg = cfg
        return cfg


class TestTradeSuspension:
    """Guards the 40310000 'new orders are rejected by user request' recovery."""

    def _engine_with(self, suspend_trade):
        eng = AlpacaExecutionEngine(api_key="k", secret_key="s")
        eng._client = _FakeClient(suspend_trade)  # bypass real TradingClient
        return eng

    def test_reads_suspended_flag(self):
        assert self._engine_with(True).trade_suspended() is True
        assert self._engine_with(False).trade_suspended() is False

    def test_reenable_clears_the_flag(self):
        eng = self._engine_with(True)
        out = eng.set_trade_suspended(False)
        assert eng._client.set_called_with is False
        assert out["suspend_trade"] is False

    def test_can_suspend_too(self):
        eng = self._engine_with(False)
        eng.set_trade_suspended(True)
        assert eng._client.set_called_with is True
