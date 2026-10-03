"""Module 2 — Alpaca Asynchronous Engine (Auto-Trading & Portfolio Connector).

Wraps the official ``alpaca-py`` SDK behind a small, safe façade and enforces
the platform's hard rule inherited from Algohns V11: **paper trading only**.
Order execution and portfolio synchronisation can run in the background via
Celery workers (see :mod:`algohns.workers.tasks`) so the strategy keeps
operating with the browser closed.

Nothing here ever touches a live-money endpoint: the engine refuses to
construct a non-paper client.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

from ..config import get_settings
from ..core.utils import lazy_import, require

_trading = lazy_import(
    "alpaca.trading.client", pip_name="alpaca-py", reason="connect to Alpaca"
)
_requests = lazy_import(
    "alpaca.trading.requests", pip_name="alpaca-py", reason="build Alpaca orders"
)
_enums = lazy_import(
    "alpaca.trading.enums", pip_name="alpaca-py", reason="use Alpaca order enums"
)


class RealMoneyLockError(RuntimeError):
    """Raised if anything attempts to execute against a live-money account."""


@dataclass
class OrderTicket:
    symbol: str
    qty: float | None = None
    notional: float | None = None
    side: Literal["buy", "sell"] = "buy"
    type: Literal["market", "limit"] = "market"
    limit_price: float | None = None
    time_in_force: Literal["day", "gtc", "ioc"] = "day"


class AlpacaExecutionEngine:
    """Thin, paper-only wrapper around ``alpaca-py``'s TradingClient."""

    def __init__(self, api_key: str | None = None, secret_key: str | None = None) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.alpaca_api_key
        self.secret_key = secret_key or settings.alpaca_secret_key
        if not settings.alpaca_paper:
            raise RealMoneyLockError(
                "Algohns is paper-only. Set ALPACA_PAPER=true (real-money execution is locked)."
            )
        self._client = None  # lazy

    # -------------------------------------------------------------- client
    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key)

    @property
    def client(self):
        if self._client is None:
            trading = require(_trading)
            if not self.configured:
                raise RuntimeError(
                    "Alpaca keys missing. Set ALPACA_API_KEY and ALPACA_SECRET_KEY."
                )
            self._client = trading.TradingClient(
                self.api_key, self.secret_key, paper=True
            )
        return self._client

    # ------------------------------------------------------------- account
    def account(self) -> dict[str, Any]:
        acct = self.client.get_account()
        # Guardrail: confirm the connected account is a paper account.
        return _to_dict(acct)

    def is_paper_account(self) -> bool:
        """Verify connectivity to the paper endpoint (client forces paper=True)."""
        try:
            self.account()
            return True
        except Exception:  # noqa: BLE001
            return False

    def clock(self) -> dict[str, Any]:
        """Market clock (open/closed, next open/close)."""
        try:
            return _to_dict(self.client.get_clock())
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def tradable_symbols(self) -> set[str]:
        """Symbols Alpaca can actually trade (active, tradable US equities).

        Alpaca only trades US-listed equities, so a foreign listing such as
        ``3690N.MX`` or ``WMT.DE`` is rejected with code 42210000 ("asset not
        found"). This lets the caller skip untradable instruments *before* an
        order is sent instead of erroring on them. Returns an empty set on any
        failure, so the caller falls back to attempting the orders (each guarded
        individually).
        """
        requests = require(_requests)
        enums = require(_enums)
        try:
            req = requests.GetAssetsRequest(
                status=enums.AssetStatus.ACTIVE,
                asset_class=enums.AssetClass.US_EQUITY,
            )
            return {a.symbol for a in self.client.get_all_assets(req)
                    if getattr(a, "tradable", False)}
        except Exception:  # noqa: BLE001
            return set()

    # ------------------------------------------------- trading suspension
    def account_configurations(self) -> dict[str, Any]:
        """Account trading configuration (shorting, fractional, suspend_trade …)."""
        return _to_dict(self.client.get_account_configurations())

    def trade_suspended(self) -> bool:
        """True when the account's ``suspend_trade`` flag blocks new orders.

        Alpaca rejects every new order with code 40310000 ("new orders are
        rejected by user request") while this flag is on. It is a per-account
        switch, not a code bug, so the app exposes reading and clearing it.
        """
        return bool(self.account_configurations().get("suspend_trade", False))

    def set_trade_suspended(self, suspended: bool) -> dict[str, Any]:
        """Turn the account's ``suspend_trade`` flag on/off (paper).

        Clearing it is how the dashboard's "suspend trading" switch is undone
        programmatically, which is what unblocks a 40310000 rejection.
        """
        cfg = self.client.get_account_configurations()
        if isinstance(cfg, dict):
            cfg["suspend_trade"] = suspended
        else:
            cfg.suspend_trade = suspended
        return _to_dict(self.client.set_account_configurations(cfg))

    # ----------------------------------------------------------- positions
    def positions(self) -> list[dict[str, Any]]:
        return [_to_dict(p) for p in self.client.get_all_positions()]

    def list_orders(self, status: str = "all", limit: int = 50) -> list[dict[str, Any]]:
        """Recent orders (execution journal)."""
        requests = require(_requests)
        enums = require(_enums)
        try:
            status_enum = {
                "all": enums.QueryOrderStatus.ALL,
                "open": enums.QueryOrderStatus.OPEN,
                "closed": enums.QueryOrderStatus.CLOSED,
            }.get(status, enums.QueryOrderStatus.ALL)
            req = requests.GetOrdersRequest(status=status_enum, limit=limit)
            return [_to_dict(o) for o in self.client.get_orders(req)]
        except Exception as exc:  # noqa: BLE001
            return [{"error": str(exc)}]

    def portfolio_snapshot(self) -> dict[str, Any]:
        """A compact, display-friendly portfolio view."""
        acct = self.account()
        positions = self.positions()
        equity = float(acct.get("equity", 0) or 0)
        alloc = []
        for p in positions:
            mv = float(p.get("market_value", 0) or 0)
            alloc.append(
                {
                    "symbol": p.get("symbol"),
                    "qty": float(p.get("qty", 0) or 0),
                    "market_value": mv,
                    "unrealized_pl": float(p.get("unrealized_pl", 0) or 0),
                    "weight": (mv / equity) if equity else 0.0,
                }
            )
        return {
            "equity": equity,
            "cash": float(acct.get("cash", 0) or 0),
            "buying_power": float(acct.get("buying_power", 0) or 0),
            "positions": alloc,
        }

    # --------------------------------------------------------------- orders
    def open_order_keys(self) -> set[tuple[str, str]]:
        """(symbol, side) of every order still open (not filled/cancelled).

        Used to avoid re-sending the *same* order while an identical one is
        still working — a duplicate buy/sell on a security whose order has not
        completed yet.
        """
        keys: set[tuple[str, str]] = set()
        for o in self.list_orders(status="open", limit=200):
            sym = str(o.get("symbol") or "").upper()
            side = str(o.get("side") or "").lower()
            side = "buy" if "buy" in side else "sell" if "sell" in side else side
            if sym:
                keys.add((sym, side))
        return keys

    def submit_order(self, ticket: OrderTicket,
                     client_order_id: str | None = None) -> dict[str, Any]:
        requests = require(_requests)
        enums = require(_enums)

        side = enums.OrderSide.BUY if ticket.side == "buy" else enums.OrderSide.SELL
        tif = {
            "day": enums.TimeInForce.DAY,
            "gtc": enums.TimeInForce.GTC,
            "ioc": enums.TimeInForce.IOC,
        }[ticket.time_in_force]

        common = dict(symbol=ticket.symbol, qty=ticket.qty, notional=ticket.notional,
                      side=side, time_in_force=tif)
        if client_order_id:
            common["client_order_id"] = client_order_id
        if ticket.type == "limit":
            if ticket.limit_price is None:
                raise ValueError("limit order requires limit_price")
            req = requests.LimitOrderRequest(limit_price=ticket.limit_price, **common)
        else:
            req = requests.MarketOrderRequest(**common)
        return _to_dict(self.client.submit_order(req))

    def preview_order(self, ticket: OrderTicket) -> dict[str, Any]:
        """Non-executing validation of an order ticket (safety preview)."""
        errors = []
        if not ticket.symbol:
            errors.append("symbol is required")
        if ticket.qty is None and ticket.notional is None:
            errors.append("either qty or notional is required")
        if ticket.type == "limit" and ticket.limit_price is None:
            errors.append("limit order needs limit_price")
        return {
            "ticket": ticket.__dict__,
            "valid": not errors,
            "errors": errors,
            "mode": "PAPER",
        }

    def cancel_all(self) -> dict[str, Any]:
        """Kill switch — cancel every open order."""
        try:
            self.client.cancel_orders()
            return {"status": "all_orders_cancelled"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": str(exc)}

    def close_all_positions(self, cancel_orders: bool = True) -> dict[str, Any]:
        """Kill switch — liquidate every position (paper)."""
        try:
            self.client.close_all_positions(cancel_orders=cancel_orders)
            return {"status": "all_positions_closed"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": str(exc)}

    # ------------------------------------------------------------- rebalance
    def rebalance_to_weights(
        self,
        target_weights: dict[str, float],
        dry_run: bool = True,
        close_untracked: bool = False,
        tradable: set[str] | None = None,
        open_keys: set[tuple[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate (and optionally submit) orders to reach target weights.

        Uses notional orders based on current equity. In ``dry_run`` mode it
        returns the plan without executing — ideal for the dashboard preview.
        When ``dry_run`` is False every planned order is submitted to the Alpaca
        **paper** account and its response (order id, status …) is attached to
        the plan entry under ``result``, with ``status`` = ``sent``.

        **Resilient by design.** Each order is submitted independently: a symbol
        Alpaca rejects (e.g. a foreign listing) is recorded on its own entry
        (``status`` = ``error``) and the batch continues, so one bad ticker can
        no longer stop every other order from going out. If ``tradable`` is
        given, symbols not in it are skipped up front (``status`` = ``skipped``)
        without an order attempt.

        With ``close_untracked`` the account is brought fully onto the target:
        any position not in ``target_weights`` is liquidated (a full quantity
        sell).
        """
        snap = self.portfolio_snapshot()
        equity = snap["equity"]
        targets = {str(s).upper(): float(w) for s, w in target_weights.items()}
        current = {p["symbol"]: float(p.get("market_value", 0.0) or 0.0)
                   for p in snap["positions"]}
        held_qty = {p["symbol"]: float(p.get("qty", 0.0) or 0.0)
                    for p in snap["positions"]}
        threshold = max(1.0, 0.001 * equity)
        plan: list[dict[str, Any]] = []
        # 1-minute idempotency window: an identical order re-submitted within the
        # same minute reuses this client_order_id, which Alpaca rejects as a
        # duplicate — so a double-click cannot place the same order twice.
        bucket = int(time.time() // 60)

        def _execute(entry: dict[str, Any], ticket: OrderTicket) -> None:
            key = (ticket.symbol, ticket.side)
            if tradable is not None and ticket.symbol not in tradable:
                entry["status"] = "skipped"
                entry["error"] = "not tradable on Alpaca (US equities only)"
            elif open_keys is not None and key in open_keys:
                entry["status"] = "duplicate"
                entry["error"] = "an open order for this symbol/side already exists"
            elif not dry_run:
                coid = f"algohns-{ticket.symbol}-{ticket.side}-{bucket}"
                try:
                    entry["result"] = self.submit_order(ticket, client_order_id=coid)
                    entry["status"] = "sent"
                    if open_keys is not None:
                        open_keys.add(key)  # block a duplicate later in this batch
                except Exception as exc:  # noqa: BLE001 - one failure ≠ abort
                    msg = f"{type(exc).__name__}: {exc}"
                    low = msg.lower()
                    dup = "client_order_id" in low or "duplicate" in low or "40010001" in msg
                    entry["status"] = "duplicate" if dup else "error"
                    entry["error"] = msg
            plan.append(entry)

        # 1) Move the target symbols toward their target notional.
        for symbol, weight in targets.items():
            target_value = equity * weight
            delta = target_value - current.get(symbol, 0.0)
            if abs(delta) < threshold:
                continue
            ticket = OrderTicket(
                symbol=symbol,
                notional=round(abs(delta), 2),
                side="buy" if delta > 0 else "sell",
            )
            _execute({"symbol": symbol, "action": "rebalance",
                      "side": ticket.side, "delta_notional": round(delta, 2)}, ticket)

        # 2) Optionally exit everything that is not part of the target.
        if close_untracked:
            for symbol, qty in held_qty.items():
                if symbol in targets or abs(qty) < 1e-9:
                    continue
                ticket = OrderTicket(
                    symbol=symbol,
                    qty=abs(qty),
                    side="sell" if qty > 0 else "buy",
                )
                _execute({"symbol": symbol, "action": "exit",
                          "side": ticket.side,
                          "delta_notional": -round(current.get(symbol, 0.0), 2)}, ticket)

        return plan


def _to_dict(obj: Any) -> dict[str, Any]:
    """Best-effort conversion of an alpaca-py model to a plain dict."""
    for attr in ("model_dump", "dict", "_raw"):
        if hasattr(obj, attr):
            try:
                val = getattr(obj, attr)
                return val() if callable(val) else dict(val)
            except Exception:  # noqa: BLE001
                continue
    if isinstance(obj, dict):
        return obj
    return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_") and not callable(getattr(obj, k))}
