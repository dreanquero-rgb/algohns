"""Standalone 24/7 trading runner for the Oracle Always-Free VM (no Celery).

Loops on a fixed interval and trades the **enabled** strategies from the shared
strategy store:

* **HFT** (section ``HFT``) — on 1-minute bars, on the main Alpaca account;
  long when the signal turns +1 and flat, close when it turns -1 and held.
* **PM** (section ``PM``) — a once-a-day rebalance at the open, on the PM
  account (``ALPACA_PM_*`` with fallback), re-screening the saved rules.

Everything is paper-only (the engine refuses a live client). Trading is OFF
unless ``ALGO_WORKER_ENABLED=true``, so merely starting the worker never places
an order — it logs what it *would* do instead.

Run on the VM:
    python -m algohns.workers.runner              # loop forever
    python -m algohns.workers.runner --once       # one cycle, then exit
    python -m algohns.workers.runner --once --dry # decide only, send nothing
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import time

from ..config import get_settings
from ..core import strategy_store as store
from ..core.data_providers import get_market_data
from ..modules import hft_strategies as hft
from ..modules import pm_screen
from ..modules.alpaca_execution import AlpacaExecutionEngine, OrderTicket

log = logging.getLogger("algohns.runner")


def _truthy(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _held_qty(engine: AlpacaExecutionEngine) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in engine.positions():
        try:
            out[str(p.get("symbol"))] = float(p.get("qty", 0) or 0)
        except Exception:  # noqa: BLE001
            continue
    return out


def _decide_hft_orders(signals: dict[str, int], held: dict[str, float],
                       notional: float) -> list[OrderTicket]:
    """Pure decision — long on +1 when flat, close on -1 when held.

    Stocks are traded long/flat only (no shorting), which also sidesteps a lot
    of borrow/PDT edge cases. Returns the order tickets to send.
    """
    tickets: list[OrderTicket] = []
    for sym, sig in signals.items():
        pos = held.get(sym, 0.0)
        if sig == 1 and pos <= 0:
            tickets.append(OrderTicket(symbol=sym, notional=float(notional), side="buy"))
        elif sig == -1 and pos > 0:
            tickets.append(OrderTicket(symbol=sym, qty=pos, side="sell"))
    return tickets


def _close_series(raw, symbol):
    if raw is None or getattr(raw, "empty", True):
        return None
    cols = getattr(raw, "columns", [])
    s = raw[symbol] if symbol in cols else raw.iloc[:, 0]
    import pandas as pd
    return pd.to_numeric(s, errors="coerce").dropna()


def run_hft_cycle(dry: bool) -> dict:
    """One HFT pass over every enabled HFT strategy (main account)."""
    settings = get_settings()
    if not settings.alpaca_configured:
        return {"hft": "skipped: main Alpaca keys not set"}
    eng = AlpacaExecutionEngine()  # main account == HFT
    clock = eng.clock()
    if not clock.get("is_open", False):
        return {"hft": "skipped: market closed", "next_open": clock.get("next_open")}

    md = get_market_data()
    held = _held_qty(eng)
    open_keys = eng.open_order_keys() if not dry else set()
    results = []
    for strat in store.list_strategies("HFT"):
        if not strat.enabled:
            continue
        p = strat.params
        builtin = p.get("builtin")
        syms = p.get("symbols", [])
        notional = float(p.get("notional", 1000))
        run_params = {k: v for k, v in p.items()
                      if k not in ("builtin", "symbols", "notional", "select")}
        signals: dict[str, int] = {}
        for sym in syms:
            try:
                close = _close_series(md.history([sym], period="5d", interval="1m"), sym)
                if close is not None and not close.empty:
                    signals[sym] = hft.latest_signal(builtin, close, run_params)
            except Exception as exc:  # noqa: BLE001
                log.warning("HFT %s %s: data error %s", strat.name, sym, exc)
        tickets = _decide_hft_orders(signals, held, notional)
        sent = 0
        for t in tickets:
            if (t.symbol, t.side) in open_keys:
                continue
            if dry:
                sent += 1
                continue
            try:
                eng.submit_order(t)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("HFT %s %s: order error %s", strat.name, t.symbol, exc)
        results.append({"strategy": strat.name, "signals": signals,
                        "orders": sent, "dry": dry})
    return {"hft": results or "no enabled HFT strategies"}


def run_pm_daily(dry: bool, state: dict) -> dict:
    """Once-a-day rebalance of every enabled PM strategy (PM account)."""
    settings = get_settings()
    key, secret = settings.pm_alpaca_keys()
    if not (key and secret):
        return {"pm": "skipped: PM Alpaca keys not set"}
    eng = AlpacaExecutionEngine(key, secret)
    clock = eng.clock()
    if not clock.get("is_open", False):
        return {"pm": "skipped: market closed"}
    today = dt.date.today().isoformat()
    if state.get("pm_last") == today:
        return {"pm": "skipped: already rebalanced today"}

    results = []
    for strat in store.list_strategies("PM"):
        if not strat.enabled:
            continue
        try:
            weights = pm_screen.weights_from_params(strat.params)
        except Exception as exc:  # noqa: BLE001
            results.append({"strategy": strat.name, "error": str(exc)})
            continue
        if not weights:
            results.append({"strategy": strat.name, "skip": "empty screen"})
            continue
        if dry:
            results.append({"strategy": strat.name, "n": len(weights), "dry": True})
        else:
            try:
                plan = eng.rebalance_to_weights(weights, dry_run=False,
                                                close_untracked=True)
                results.append({"strategy": strat.name, "n": len(weights),
                                "orders": len(plan)})
            except Exception as exc:  # noqa: BLE001
                results.append({"strategy": strat.name, "error": str(exc)})
    state["pm_last"] = today
    return {"pm": results or "no enabled PM strategies"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Algohns 24/7 trading runner")
    ap.add_argument("--once", action="store_true", help="run one cycle then exit")
    ap.add_argument("--dry", action="store_true", help="decide only, send nothing")
    ap.add_argument("--interval", type=int,
                    default=int(os.getenv("ALGO_WORKER_INTERVAL", "60") or 60),
                    help="seconds between HFT cycles (default 60)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    enabled = _truthy("ALGO_WORKER_ENABLED", False)
    dry = args.dry or not enabled
    if not enabled:
        log.warning("ALGO_WORKER_ENABLED is not true → DRY mode "
                    "(no orders will be sent). Set it to true on the VM to trade.")

    state: dict = {}
    while True:
        try:
            log.info("%s", run_hft_cycle(dry))
            log.info("%s", run_pm_daily(dry, state))
        except Exception as exc:  # noqa: BLE001
            log.exception("cycle error: %s", exc)
        if args.once:
            break
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    main()
