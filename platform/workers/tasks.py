"""Background jobs: portfolio sync, drift check, scheduled rebalance.

Every job is written to be safe to run when the market is closed and safe to
run twice, *except* `execute_rebalance`, which trades. That one requires an
explicit `confirm_token` so a stray scheduler tick or a retry cannot place
orders on its own.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from modules.alpaca_execution import AlpacaConnector, OrderIntent, OrderSide, OrderType
from workers.celery_app import celery_app

log = logging.getLogger(__name__)

# Guard against an unattended scheduler placing real (paper) orders.
REBALANCE_CONFIRM_TOKEN = "EXECUTE"


def _task(fn):
    """Register with Celery when available, else leave the plain function.

    Keeps job bodies importable and directly callable in tests and from
    Streamlit without a broker running.
    """
    if celery_app is None:
        return fn
    return celery_app.task(name=f"workers.tasks.{fn.__name__}", bind=False)(fn)


@_task
def sync_portfolio() -> dict[str, Any]:
    """Fetch and record the current portfolio state. Read-only."""
    conn = AlpacaConnector()
    snap = conn.snapshot()
    payload = {
        "fetched_at": snap.fetched_at.isoformat(),
        "equity": snap.equity,
        "cash": snap.cash,
        "buying_power": snap.buying_power,
        "invested_value": snap.invested_value,
        "concentration": snap.concentration,
        "positions": [asdict(p) for p in snap.positions],
        "trading_blocked": snap.trading_blocked,
    }
    log.info(
        "portfolio sync: equity=%.2f positions=%d", snap.equity, len(snap.positions)
    )
    return payload


@_task
def check_drift(
    target_weights: dict[str, float] | None = None, min_drift: float = 0.005
) -> dict[str, Any]:
    """Report how far the book has drifted from target. Places no orders."""
    if not target_weights:
        return {"status": "no_target", "message": "Nessun target configurato."}

    conn = AlpacaConnector()
    plan = conn.build_rebalance(target_weights, min_drift=min_drift)
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "needs_rebalance": not plan.is_empty,
        "turnover": plan.turnover,
        "drifts": plan.drifts,
        "proposed_orders": plan.describe(),
        "skipped": plan.skipped,
    }


@_task
def execute_rebalance(
    target_weights: dict[str, float],
    confirm_token: str = "",
    *,
    min_drift: float = 0.005,
    require_market_open: bool = True,
) -> dict[str, Any]:
    """Rebalance to `target_weights`. **Trades.**

    Refuses without the exact confirm token, so neither a scheduler
    misconfiguration nor a task retry can trade unattended.
    """
    if confirm_token != REBALANCE_CONFIRM_TOKEN:
        return {
            "status": "refused",
            "message": (
                "Rebalance non confermato. Serve "
                f"confirm_token={REBALANCE_CONFIRM_TOKEN!r}."
            ),
        }

    conn = AlpacaConnector()
    if require_market_open and not conn.is_market_open():
        return {"status": "market_closed", "message": "Mercato chiuso: nessun ordine."}

    plan = conn.build_rebalance(target_weights, min_drift=min_drift)
    if plan.is_empty:
        return {"status": "in_balance", "turnover": 0.0, "results": []}

    results = conn.submit_all(plan.intents, stop_on_error=True)
    accepted = [r for r in results if r.accepted]
    return {
        "status": "executed" if len(accepted) == len(results) else "partial",
        "turnover": plan.turnover,
        "submitted": len(results),
        "accepted": len(accepted),
        "results": [r.summary for r in results],
    }


@_task
def kill_switch() -> dict[str, Any]:
    """Cancel every open order and flatten every position."""
    conn = AlpacaConnector()
    cancelled = conn.cancel_all()
    closed = conn.close_all_positions()
    log.warning("KILL SWITCH: %d ordini annullati, %d posizioni chiuse", cancelled, closed)
    return {
        "status": "flattened",
        "cancelled_orders": cancelled,
        "closed_positions": closed,
        "at": datetime.now(timezone.utc).isoformat(),
    }


@_task
def submit_single(
    symbol: str,
    side: str,
    notional: float | None = None,
    qty: float | None = None,
    order_type: str = "market",
    limit_price: float | None = None,
) -> dict[str, Any]:
    """Submit one order from primitive arguments (JSON-serialisable for Celery)."""
    intent = OrderIntent(
        symbol=symbol,
        side=OrderSide(side),
        notional=notional,
        qty=qty,
        order_type=OrderType(order_type),
        limit_price=limit_price,
    )
    result = AlpacaConnector().submit(intent)
    return {
        "accepted": result.accepted,
        "order_id": result.order_id,
        "status": result.status,
        "error": result.error,
        "summary": result.summary,
    }
