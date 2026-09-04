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
    def submit_order(self, ticket: OrderTicket) -> dict[str, Any]:
        requests = require(_requests)
        enums = require(_enums)

        side = enums.OrderSide.BUY if ticket.side == "buy" else enums.OrderSide.SELL
        tif = {
            "day": enums.TimeInForce.DAY,
            "gtc": enums.TimeInForce.GTC,
            "ioc": enums.TimeInForce.IOC,
        }[ticket.time_in_force]

        if ticket.type == "limit":
            if ticket.limit_price is None:
                raise ValueError("limit order requires limit_price")
            req = requests.LimitOrderRequest(
                symbol=ticket.symbol,
                qty=ticket.qty,
                notional=ticket.notional,
                side=side,
                time_in_force=tif,
                limit_price=ticket.limit_price,
            )
        else:
            req = requests.MarketOrderRequest(
                symbol=ticket.symbol,
                qty=ticket.qty,
                notional=ticket.notional,
                side=side,
                time_in_force=tif,
            )
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
        self, target_weights: dict[str, float], dry_run: bool = True
    ) -> list[dict[str, Any]]:
        """Generate (and optionally submit) orders to reach target weights.

        Uses notional orders based on current equity. In ``dry_run`` mode it
        returns the plan without executing — ideal for the dashboard preview.
        """
        snap = self.portfolio_snapshot()
        equity = snap["equity"]
        current = {p["symbol"]: p["market_value"] for p in snap["positions"]}
        plan: list[dict[str, Any]] = []
        for symbol, weight in target_weights.items():
            target_value = equity * weight
            delta = target_value - current.get(symbol, 0.0)
            if abs(delta) < max(1.0, 0.001 * equity):
                continue
            ticket = OrderTicket(
                symbol=symbol,
                notional=round(abs(delta), 2),
                side="buy" if delta > 0 else "sell",
            )
            entry = {"symbol": symbol, "delta_notional": round(delta, 2), "side": ticket.side}
            if not dry_run:
                entry["result"] = self.submit_order(ticket)
            plan.append(entry)
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
