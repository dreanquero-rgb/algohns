"""Target allocation for the headless auto-trading worker.

The dashboard's Strategy Lab lets a human build an allocation interactively;
the background worker has no human, so it reads its target from configuration
instead. Two ways to set it, checked in this order:

1. ``ALGO_TARGET_WEIGHTS`` — an explicit JSON allocation, e.g.
   ``{"SPY": 0.6, "AGG": 0.4}``. Whatever you put here is what it trades.
2. ``ALGO_STRATEGY`` — the name of one of the presets below.

Both live in ``.env`` alongside the keys, so choosing a strategy is a config
change, not a code change, and nothing here ever touches a credential.

The presets are broad, liquid US ETFs on purpose: they are what a paper
demonstration of periodic rebalancing should trade, not a stock-picking bet.
Weights are normalised on load, so they need not sum to exactly 1.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

__all__ = ["PRESETS", "resolve_target_weights", "StrategyError"]


class StrategyError(ValueError):
    """Raised when the configured strategy cannot be resolved to weights."""


# Named allocations. Each maps a liquid ETF to a target portfolio weight.
PRESETS: dict[str, dict[str, float]] = {
    # Classic 60/40 equity/bond.
    "balanced": {"SPY": 0.60, "AGG": 0.40},
    # Equity-tilted, growth flavour.
    "growth": {"SPY": 0.45, "QQQ": 0.35, "EFA": 0.20},
    # Bond-heavy, defensive.
    "conservative": {"AGG": 0.55, "SPY": 0.30, "GLD": 0.15},
    # A simplified all-weather sleeve (Dalio-style risk spread).
    "all_weather": {"SPY": 0.30, "TLT": 0.40, "IEF": 0.15, "GLD": 0.075,
                    "DBC": 0.075},
    # Global equity, market-cap-ish split.
    "global_equity": {"SPY": 0.55, "EFA": 0.30, "EEM": 0.15},
}


def _normalise(weights: dict[str, float]) -> dict[str, float]:
    """Drop non-positive weights and rescale the rest to sum to 1."""
    clean = {str(k).upper().strip(): float(v)
             for k, v in weights.items() if float(v) > 0}
    total = sum(clean.values())
    if not clean or total <= 0:
        raise StrategyError("target weights must contain a positive allocation")
    return {k: v / total for k, v in clean.items()}


def resolve_target_weights(
    preset: str = "balanced", weights_json: str = "",
) -> dict[str, float]:
    """Resolve the worker's target allocation from configuration.

    Explicit JSON wins over a preset name. The result is always normalised.
    """
    raw = (weights_json or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StrategyError(f"ALGO_TARGET_WEIGHTS is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise StrategyError("ALGO_TARGET_WEIGHTS must be a JSON object of "
                                '{"TICKER": weight}')
        return _normalise(parsed)

    key = (preset or "").lower().strip()
    if key not in PRESETS:
        raise StrategyError(
            f"unknown strategy preset {preset!r}; choose from {sorted(PRESETS)} "
            "or set ALGO_TARGET_WEIGHTS to an explicit JSON allocation"
        )
    return _normalise(PRESETS[key])
