"""Tests for the headless auto-trading worker.

The worker can place real (paper) orders unattended, so its safety gates are
the part that most needs pinning: it must not trade unless keys are present,
the operator opted in, and the market is actually open. No Celery and no
network here — a stub engine stands in for Alpaca, and settings are injected.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from algohns.workers import tasks
from algohns.workers.strategy import (
    PRESETS,
    StrategyError,
    resolve_target_weights,
)


# --------------------------------------------------------------- strategy
class TestStrategyResolution:
    def test_presets_are_normalised(self):
        for name in PRESETS:
            w = resolve_target_weights(name)
            assert sum(w.values()) == pytest.approx(1.0)
            assert all(v > 0 for v in w.values())

    def test_json_override_wins_over_preset(self):
        w = resolve_target_weights("balanced", '{"VTI": 1.0}')
        assert w == {"VTI": 1.0}

    def test_weights_need_not_sum_to_one(self):
        w = resolve_target_weights("balanced", '{"SPY": 6, "AGG": 4}')
        assert w == {"SPY": pytest.approx(0.6), "AGG": pytest.approx(0.4)}

    def test_tickers_are_upper_cased(self):
        assert set(resolve_target_weights("balanced", '{"spy": 1}')) == {"SPY"}

    def test_bad_json_is_rejected(self):
        with pytest.raises(StrategyError, match="valid JSON"):
            resolve_target_weights("balanced", "{not json}")

    def test_non_object_json_is_rejected(self):
        with pytest.raises(StrategyError, match="JSON object"):
            resolve_target_weights("balanced", "[1, 2, 3]")

    def test_unknown_preset_is_rejected(self):
        with pytest.raises(StrategyError, match="unknown strategy preset"):
            resolve_target_weights("moon_shot")

    def test_all_zero_allocation_is_rejected(self):
        with pytest.raises(StrategyError, match="positive allocation"):
            resolve_target_weights("balanced", '{"SPY": 0, "AGG": -1}')


# ------------------------------------------------------------ stub engine
class _StubEngine:
    """Minimal stand-in for AlpacaExecutionEngine, records what it was asked."""

    def __init__(self, *, configured=True, is_open=True):
        self._configured = configured
        self._is_open = is_open
        self.rebalanced_with: dict | None = None

    @property
    def configured(self) -> bool:
        return self._configured

    def clock(self) -> dict:
        return {"is_open": self._is_open, "next_open": "2026-10-01T13:30:00Z"}

    def rebalance_to_weights(self, weights, dry_run=True):
        assert dry_run is False, "the scheduled task must trade for real, not dry-run"
        self.rebalanced_with = dict(weights)
        return [{"symbol": s, "side": "buy", "delta_notional": 100.0}
                for s in weights]


def _settings(**over):
    base = dict(auto_rebalance=True, strategy_preset="balanced",
                target_weights_json="", rebalance_cron="35 14 * * 1-5",
                sync_interval_seconds=300)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def patched_settings(monkeypatch):
    def _apply(**over):
        monkeypatch.setattr(tasks, "get_settings", lambda: _settings(**over))
    return _apply


# ---------------------------------------------------- the four safety gates
class TestScheduledRebalanceGates:
    def test_trades_when_enabled_and_market_open(self, patched_settings):
        patched_settings(auto_rebalance=True)
        eng = _StubEngine(configured=True, is_open=True)
        out = tasks._scheduled_rebalance(engine=eng)
        assert out["status"] == "ok"
        assert eng.rebalanced_with == {"SPY": pytest.approx(0.6), "AGG": pytest.approx(0.4)}
        assert out["n_orders"] == 2

    def test_no_keys_means_no_trade(self, patched_settings):
        patched_settings(auto_rebalance=True)
        eng = _StubEngine(configured=False)
        out = tasks._scheduled_rebalance(engine=eng)
        assert out["status"] == "skipped" and "keys" in out["reason"]
        assert eng.rebalanced_with is None

    def test_disabled_by_default_means_no_trade(self, patched_settings):
        """Bringing the stack up must never place an order on its own."""
        patched_settings(auto_rebalance=False)
        eng = _StubEngine(configured=True, is_open=True)
        out = tasks._scheduled_rebalance(engine=eng)
        assert out["status"] == "skipped" and "disabled" in out["reason"]
        assert eng.rebalanced_with is None

    def test_closed_market_means_no_trade(self, patched_settings):
        patched_settings(auto_rebalance=True)
        eng = _StubEngine(configured=True, is_open=False)
        out = tasks._scheduled_rebalance(engine=eng)
        assert out["status"] == "skipped" and out["reason"] == "market closed"
        assert eng.rebalanced_with is None
        assert out["next_open"]

    def test_bad_strategy_is_reported_not_traded(self, patched_settings):
        patched_settings(auto_rebalance=True, strategy_preset="nonexistent")
        eng = _StubEngine(configured=True, is_open=True)
        out = tasks._scheduled_rebalance(engine=eng)
        assert out["status"] == "error"
        assert eng.rebalanced_with is None

    def test_json_override_is_what_gets_traded(self, patched_settings):
        patched_settings(auto_rebalance=True,
                         target_weights_json='{"QQQ": 0.5, "TLT": 0.5}')
        eng = _StubEngine(configured=True, is_open=True)
        out = tasks._scheduled_rebalance(engine=eng)
        assert eng.rebalanced_with == {"QQQ": 0.5, "TLT": 0.5}
        assert out["strategy"] == "custom"


class TestModuleWiring:
    def test_worker_exposes_the_scheduled_task(self):
        # Celery is absent in the lean venv, so this is the plain fallback.
        assert callable(tasks.scheduled_rebalance)

    def test_sync_portfolio_skips_without_keys(self, monkeypatch):
        class _NoKeys(_StubEngine):
            def __init__(self):
                super().__init__(configured=False)
        monkeypatch.setattr(tasks, "AlpacaExecutionEngine", _NoKeys)
        assert tasks._sync_portfolio()["status"] == "skipped"
