"""MODULE 2 — Alpaca Asynchronous Execution & Portfolio Connector.

Wraps `alpaca-py` with a paper-trading lock, order pre-flight validation and
a portfolio reconciliation routine, then exposes those as background jobs so
rebalancing continues with the browser closed.

The paper lock is enforced at the *host* level, not by a config flag: the
resolved base URL must be a known paper host or the client refuses to
construct. This mirrors the existing Cloudflare worker's behaviour, so the
Python and JS halves of the platform cannot disagree about whether live
trading is possible.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable
from urllib.parse import urlparse

from core.config import ConfigError, settings

__all__ = [
    "AlpacaConnector",
    "OrderIntent",
    "OrderSide",
    "OrderType",
    "TimeInForce",
    "ExecutionResult",
    "PortfolioSnapshot",
    "RebalancePlan",
    "LiveTradingBlocked",
    "require_paper",
]

log = logging.getLogger(__name__)

# Hosts considered paper. Anything else is refused.
PAPER_HOSTS = frozenset({"paper-api.alpaca.markets"})


class LiveTradingBlocked(RuntimeError):
    """Raised when a non-paper endpoint is configured."""


def require_paper(base_url: str) -> str:
    """Assert `base_url` points at Alpaca paper trading.

    Checked on hostname rather than substring so a URL like
    `https://evil.example/paper-api.alpaca.markets` cannot slip through.
    """
    host = (urlparse(base_url).hostname or "").lower()
    if host not in PAPER_HOSTS:
        raise LiveTradingBlocked(
            f"Endpoint non-paper rifiutato: {base_url!r} (host {host!r}). "
            f"Questa piattaforma opera solo su {sorted(PAPER_HOSTS)}. "
            "L'esecuzione con denaro reale e' bloccata by design."
        )
    return base_url


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"
    OPG = "opg"
    CLS = "cls"


@dataclass(frozen=True)
class OrderIntent:
    """A validated order request, independent of the SDK's own types.

    Keeping an internal representation means the backtest suite and the
    rebalancer can emit orders without importing alpaca-py, and the same
    intents can be dry-run rendered in the UI before anything is submitted.
    """

    symbol: str
    side: OrderSide
    qty: float | None = None
    notional: float | None = None
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: float | None = None
    stop_price: float | None = None
    client_order_id: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol is required")
        if (self.qty is None) == (self.notional is None):
            raise ValueError(
                f"{self.symbol}: specify exactly one of qty or notional"
            )
        if self.qty is not None and self.qty <= 0:
            raise ValueError(f"{self.symbol}: qty must be positive, got {self.qty}")
        if self.notional is not None and self.notional <= 0:
            raise ValueError(
                f"{self.symbol}: notional must be positive, got {self.notional}"
            )
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            if self.limit_price is None or self.limit_price <= 0:
                raise ValueError(
                    f"{self.symbol}: {self.order_type.value} requires a positive limit_price"
                )
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            if self.stop_price is None or self.stop_price <= 0:
                raise ValueError(
                    f"{self.symbol}: {self.order_type.value} requires a positive stop_price"
                )
        # Fractional quantities are only supported for market DAY orders.
        if (
            self.qty is not None
            and self.qty != int(self.qty)
            and not (
                self.order_type is OrderType.MARKET
                and self.time_in_force is TimeInForce.DAY
            )
        ):
            raise ValueError(
                f"{self.symbol}: le quantita' frazionarie richiedono "
                "ordine market con TIF day"
            )

    def describe(self) -> str:
        size = f"{self.qty} sh" if self.qty is not None else f"${self.notional:,.2f}"
        px = ""
        if self.limit_price is not None:
            px += f" limit {self.limit_price}"
        if self.stop_price is not None:
            px += f" stop {self.stop_price}"
        return (
            f"{self.side.value.upper()} {size} {self.symbol} "
            f"[{self.order_type.value}/{self.time_in_force.value}]{px}"
        )


@dataclass
class ExecutionResult:
    intent: OrderIntent
    accepted: bool
    order_id: str | None = None
    status: str | None = None
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    error: str | None = None
    submitted_at: datetime | None = None

    @property
    def summary(self) -> str:
        if not self.accepted:
            return f"RIFIUTATO {self.intent.symbol}: {self.error}"
        return (
            f"{self.intent.symbol} {self.status} "
            f"({self.filled_qty} @ {self.filled_avg_price})"
        )


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float
    current_price: float

    @property
    def side(self) -> str:
        return "long" if self.qty > 0 else "short"


@dataclass
class PortfolioSnapshot:
    equity: float
    cash: float
    buying_power: float
    positions: list[Position] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    account_blocked: bool = False
    trading_blocked: bool = False
    pattern_day_trader: bool = False

    @property
    def invested_value(self) -> float:
        return sum(p.market_value for p in self.positions)

    @property
    def weights(self) -> dict[str, float]:
        """Position weights on equity. Empty when equity is non-positive."""
        if self.equity <= 0:
            return {}
        return {p.symbol: p.market_value / self.equity for p in self.positions}

    @property
    def concentration(self) -> float:
        """Herfindahl index of position weights; 1.0 means single-name."""
        w = list(self.weights.values())
        return sum(x * x for x in w) if w else 0.0


@dataclass
class RebalancePlan:
    """Orders needed to move from current weights to target weights."""

    intents: list[OrderIntent]
    turnover: float
    drifts: dict[str, float]
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.intents

    def describe(self) -> list[str]:
        return [i.describe() for i in self.intents]


class AlpacaConnector:
    """Paper-only Alpaca trading client.

    `alpaca-py` is imported lazily so the rest of the platform (bond engine,
    SEC modules, backtests) works without the trading SDK installed.
    """

    def __init__(
        self,
        key_id: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.base_url = require_paper(base_url or settings.alpaca_base_url)
        if key_id and secret_key:
            self._key, self._secret = key_id, secret_key
        else:
            self._key, self._secret = settings.require_alpaca()
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from alpaca.trading.client import TradingClient  # type: ignore
            except ImportError as exc:
                raise ConfigError(
                    "alpaca-py non installato. `pip install alpaca-py`."
                ) from exc
            # paper=True is belt-and-braces alongside require_paper().
            self._client = TradingClient(self._key, self._secret, paper=True)
        return self._client

    # ---------------------------------------------------------------- account

    def snapshot(self) -> PortfolioSnapshot:
        """Current account and positions."""
        acct = self.client.get_account()
        raw_positions = self.client.get_all_positions()
        positions = [
            Position(
                symbol=p.symbol,
                qty=float(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                market_value=float(p.market_value),
                unrealized_pl=float(p.unrealized_pl or 0.0),
                unrealized_plpc=float(p.unrealized_plpc or 0.0),
                current_price=float(p.current_price or 0.0),
            )
            for p in raw_positions
        ]
        return PortfolioSnapshot(
            equity=float(acct.equity),
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
            positions=positions,
            account_blocked=bool(getattr(acct, "account_blocked", False)),
            trading_blocked=bool(getattr(acct, "trading_blocked", False)),
            pattern_day_trader=bool(getattr(acct, "pattern_day_trader", False)),
        )

    def is_market_open(self) -> bool:
        try:
            return bool(self.client.get_clock().is_open)
        except Exception as exc:  # network/SDK failure should not crash a job
            log.warning("clock check failed: %s", exc)
            return False

    # ---------------------------------------------------------------- orders

    def _to_sdk_request(self, intent: OrderIntent) -> Any:
        from alpaca.trading.enums import (  # type: ignore
            OrderSide as SdkSide,
            TimeInForce as SdkTif,
        )
        from alpaca.trading.requests import (  # type: ignore
            LimitOrderRequest,
            MarketOrderRequest,
            StopLimitOrderRequest,
            StopOrderRequest,
        )

        common = dict(
            symbol=intent.symbol.upper(),
            side=SdkSide(intent.side.value),
            time_in_force=SdkTif(intent.time_in_force.value),
            client_order_id=intent.client_order_id,
        )
        if intent.qty is not None:
            common["qty"] = intent.qty
        else:
            common["notional"] = intent.notional

        if intent.order_type is OrderType.MARKET:
            return MarketOrderRequest(**common)
        if intent.order_type is OrderType.LIMIT:
            return LimitOrderRequest(limit_price=intent.limit_price, **common)
        if intent.order_type is OrderType.STOP:
            return StopOrderRequest(stop_price=intent.stop_price, **common)
        return StopLimitOrderRequest(
            limit_price=intent.limit_price, stop_price=intent.stop_price, **common
        )

    def submit(self, intent: OrderIntent, *, dry_run: bool = False) -> ExecutionResult:
        """Submit one order. `dry_run` validates without touching the network."""
        if dry_run:
            return ExecutionResult(
                intent=intent, accepted=True, status="dry_run",
                submitted_at=datetime.now(timezone.utc),
            )
        try:
            order = self.client.submit_order(self._to_sdk_request(intent))
        except Exception as exc:
            log.warning("order rejected for %s: %s", intent.symbol, exc)
            return ExecutionResult(intent=intent, accepted=False, error=str(exc))
        return ExecutionResult(
            intent=intent,
            accepted=True,
            order_id=str(order.id),
            status=str(getattr(order.status, "value", order.status)),
            filled_qty=float(order.filled_qty or 0.0),
            filled_avg_price=(
                float(order.filled_avg_price) if order.filled_avg_price else None
            ),
            submitted_at=datetime.now(timezone.utc),
        )

    def submit_all(
        self, intents: Iterable[OrderIntent], *, dry_run: bool = False,
        stop_on_error: bool = False,
    ) -> list[ExecutionResult]:
        """Submit a batch, optionally halting at the first rejection.

        Sells are sent before buys so proceeds are available as buying power
        for the buy leg of a rebalance.
        """
        ordered = sorted(
            intents, key=lambda i: 0 if i.side is OrderSide.SELL else 1
        )
        results: list[ExecutionResult] = []
        for intent in ordered:
            res = self.submit(intent, dry_run=dry_run)
            results.append(res)
            if stop_on_error and not res.accepted:
                log.error("batch halted at %s", intent.symbol)
                break
        return results

    def cancel_all(self) -> int:
        try:
            cancelled = self.client.cancel_orders()
            return len(cancelled) if cancelled else 0
        except Exception as exc:
            log.warning("cancel_all failed: %s", exc)
            return 0

    def close_all_positions(self, *, cancel_orders: bool = True) -> int:
        """Flatten the book. This is the kill-switch primitive."""
        try:
            closed = self.client.close_all_positions(cancel_orders=cancel_orders)
            return len(closed) if closed else 0
        except Exception as exc:
            log.warning("close_all_positions failed: %s", exc)
            return 0

    # ------------------------------------------------------------ rebalancing

    def build_rebalance(
        self,
        target_weights: dict[str, float],
        *,
        snapshot: PortfolioSnapshot | None = None,
        min_drift: float = 0.005,
        min_notional: float = 1.0,
        liquidate_unlisted: bool = True,
    ) -> RebalancePlan:
        """Compute the orders needed to reach `target_weights`.

        Positions absent from the target are liquidated when
        `liquidate_unlisted` is set, which is what makes this a true
        rebalance rather than a top-up. Names whose drift is under
        `min_drift` are skipped to avoid churning on noise.
        """
        snap = snapshot or self.snapshot()
        if snap.equity <= 0:
            raise ValueError(f"equity non positiva ({snap.equity}), impossibile ribilanciare")

        total = sum(target_weights.values())
        if total > 1.0 + 1e-6:
            raise ValueError(f"i pesi target sommano a {total:.4f} > 1.0")
        if any(w < 0 for w in target_weights.values()):
            raise ValueError("pesi target negativi non supportati (no short)")

        current = snap.weights
        universe = set(target_weights) | set(current)
        intents: list[OrderIntent] = []
        drifts: dict[str, float] = {}
        skipped: dict[str, str] = {}
        gross_traded = 0.0

        for symbol in sorted(universe):
            tgt = target_weights.get(symbol, 0.0 if liquidate_unlisted else current.get(symbol, 0.0))
            cur = current.get(symbol, 0.0)
            drift = tgt - cur
            drifts[symbol] = drift

            if abs(drift) < min_drift:
                skipped[symbol] = f"drift {drift:+.3%} sotto soglia {min_drift:.2%}"
                continue

            notional = abs(drift) * snap.equity
            if notional < min_notional:
                skipped[symbol] = f"notional ${notional:.2f} sotto ${min_notional:.2f}"
                continue

            side = OrderSide.BUY if drift > 0 else OrderSide.SELL
            # Selling to zero uses share qty so the position closes exactly;
            # notional sells can leave dust behind.
            if side is OrderSide.SELL and tgt == 0.0:
                pos = next((p for p in snap.positions if p.symbol == symbol), None)
                if pos is None or pos.qty <= 0:
                    skipped[symbol] = "nessuna posizione long da liquidare"
                    continue
                intents.append(
                    OrderIntent(symbol=symbol, side=side, qty=abs(pos.qty))
                )
            else:
                intents.append(
                    OrderIntent(symbol=symbol, side=side, notional=round(notional, 2))
                )
            gross_traded += notional

        return RebalancePlan(
            intents=intents,
            turnover=gross_traded / snap.equity,
            drifts=drifts,
            skipped=skipped,
        )
