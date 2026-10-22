"""RFQ dealer simulation and the trader's blotter / P&L.

This is the execution layer on top of :mod:`algohns.modules.rate_world`. The
world gives a live **fair** mid for each bond; this module

1. turns that mid into a panel of **dealer quotes** — several banks, each with
   its own spread and a possible *axe* (an inventory skew that makes them
   aggressive on one side), and some that simply do not respond (a "no quote"),
   exactly like firing an RFQ on a dealer-to-client venue, and
2. keeps the **blotter**: positions netted per bond, realized P&L booked on
   every closing fill, and live mark-to-market against the moving mid. The
   trader's P&L therefore updates on *every transaction* and keeps ticking
   between them as the market moves.

Pure and deterministic given an RNG (no Streamlit, no threads), so the whole
thing is unit-testable. Paper only — there is no venue and no money.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Composite half-spread in price points per 100 face, before the per-dealer
# jitter, keyed by how liquid the market is. EM names are much wider.
_BASE_HALF_SPREAD = {
    "Core EU": 0.04, "Periphery EU": 0.09, "Anglo": 0.05,
    "Japan": 0.06, "LatAm EM": 0.45, "EMEA EM": 0.55,
}
# Difficulty widens spreads and the axe dispersion: a calmer tape is tighter.
_DIFFICULTY_SPREAD = {"Calmo": 0.7, "Normale": 1.0, "Volatile": 1.8}

DEALERS: tuple[str, ...] = (
    "Falcon Capital", "Meridian Bank", "Northwind Securities",
    "Blackgate Partners", "Stellar IB", "Onyx Markets",
)


@dataclass(frozen=True)
class DealerQuote:
    """One dealer's two-way price for the requested bond and size."""

    dealer: str
    responded: bool          # a dealer may decline to quote ("no quote")
    bid: float               # price per 100 face the dealer BUYS at (you hit)
    ask: float               # price per 100 face the dealer SELLS at (you lift)
    size_mm: float           # size (in millions face) the dealer will do
    latency_ms: int          # flavour: how fast the quote came back


@dataclass
class RfqResult:
    """The outcome of one request for quote."""

    bond_code: str
    bond_name: str
    mid_price: float         # composite mid shown to the trader (noisy)
    fair_price: float        # the world's TRUE mid (hidden until reveal)
    quotes: list[DealerQuote]

    @property
    def live_quotes(self) -> list[DealerQuote]:
        return [q for q in self.quotes if q.responded]

    @property
    def best_bid(self) -> DealerQuote | None:
        live = self.live_quotes
        return max(live, key=lambda q: q.bid) if live else None

    @property
    def best_ask(self) -> DealerQuote | None:
        live = self.live_quotes
        return min(live, key=lambda q: q.ask) if live else None


def generate_quotes(
    *, bond_code: str, bond_name: str, fair_price: float, region: str,
    size_mm: float, difficulty: str, rng: np.random.Generator,
    n_dealers: int = 5,
) -> RfqResult:
    """Build a dealer panel around the world's fair price.

    The trader never sees ``fair_price`` directly: the composite ``mid_price``
    carries a little display noise, and each dealer quotes around *that* with
    its own half-spread and an axe. Larger size is shown a slightly worse
    (wider) market, as on a real venue.
    """
    spread_mult = _DIFFICULTY_SPREAD.get(difficulty, 1.0)
    base_half = _BASE_HALF_SPREAD.get(region, 0.10) * spread_mult
    # Bigger clips cost more: widen ~10% per extra million over 1mm.
    base_half *= 1.0 + 0.10 * max(0.0, size_mm - 1.0)

    # Composite mid the trader sees = fair + small symmetric display noise.
    mid_price = fair_price + rng.normal(0.0, base_half * 0.25)

    chosen = list(DEALERS[:max(2, n_dealers)])
    quotes: list[DealerQuote] = []
    for name in chosen:
        responded = rng.random() > 0.15        # ~15% chance of no quote
        half = base_half * rng.uniform(0.8, 1.7)
        axe = rng.normal(0.0, base_half * 0.6)  # inventory skew, both sides
        dmid = mid_price + axe + rng.normal(0.0, base_half * 0.15)
        quotes.append(DealerQuote(
            dealer=name,
            responded=responded,
            bid=round(dmid - half, 4),
            ask=round(dmid + half, 4),
            size_mm=round(size_mm * rng.uniform(0.6, 1.0), 2),
            latency_ms=int(rng.uniform(40, 650)),
        ))
    # Guarantee at least two live quotes so an RFQ is never dead on arrival.
    live = [q for q in quotes if q.responded]
    if len(live) < 2:
        for i, q in enumerate(quotes):
            if not q.responded:
                quotes[i] = DealerQuote(q.dealer, True, q.bid, q.ask,
                                        q.size_mm, q.latency_ms)
            if sum(x.responded for x in quotes) >= 2:
                break

    return RfqResult(bond_code=bond_code, bond_name=bond_name,
                     mid_price=round(mid_price, 4),
                     fair_price=round(fair_price, 4), quotes=quotes)


# ---------------------------------------------------------------------------
# Blotter / positions / P&L
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    """A booked fill. ``notional_mm`` is signed: + buy / long, - sell / short."""

    seq: int
    bond_code: str
    bond_name: str
    side: str                # "LIFT" (buy) | "HIT" (sell)
    price: float             # execution price per 100 face
    notional_mm: float       # signed, millions face
    fair_price: float        # world fair at execution (for edge scoring)
    edge_eur: float          # mark-to-fair P&L captured at the fill
    realized_eur: float      # realized P&L this fill booked by closing
    ts: float


@dataclass
class Position:
    """Netted position in one bond."""

    bond_code: str
    bond_name: str
    net_mm: float = 0.0      # signed millions face (+ long / - short)
    avg_price: float = 0.0   # VWAP of the currently-open side
    realized_eur: float = 0.0

    def market_value_pnl(self, mark_price: float) -> float:
        """Unrealized P&L (EUR) marking the open position to ``mark_price``."""
        if self.net_mm == 0.0:
            return 0.0
        # Price is per 100 face; 1mm face = 1_000_000, so /100 * 1e6 = *1e4.
        return (mark_price - self.avg_price) * self.net_mm * 1e4


class Blotter:
    """The trader's book: positions, realized P&L, and a trade log.

    One instance lives in Streamlit ``session_state`` per run. Every fill goes
    through :meth:`fill`, which nets against any existing position and books
    realized P&L the moment a position is reduced or closed — that is the P&L
    that updates on every transaction.
    """

    def __init__(self, start_cash_eur: float = 0.0) -> None:
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.realized_eur: float = 0.0
        self._seq = 0

    # ----------------------------------------------------------------- fill
    def fill(
        self, *, bond_code: str, bond_name: str, side: str, price: float,
        notional_mm: float, fair_price: float, ts: float,
    ) -> Trade:
        """Book a fill. ``notional_mm`` signed (+buy / -sell). Returns the Trade."""
        pos = self.positions.setdefault(
            bond_code, Position(bond_code=bond_code, bond_name=bond_name))

        realized = self._apply(pos, price, notional_mm)
        self.realized_eur += realized

        # Edge vs fair: did you trade on the right side of the true mid?
        # Buying below fair (price < fair) or selling above fair both earn edge.
        edge = (fair_price - price) * notional_mm * 1e4

        self._seq += 1
        trade = Trade(
            seq=self._seq, bond_code=bond_code, bond_name=bond_name, side=side,
            price=round(price, 4), notional_mm=round(notional_mm, 3),
            fair_price=round(fair_price, 4), edge_eur=edge,
            realized_eur=realized, ts=ts,
        )
        self.trades.append(trade)
        return trade

    @staticmethod
    def _apply(pos: Position, price: float, q: float) -> float:
        """Net ``q`` (signed) into ``pos`` at ``price``; return realized EUR."""
        realized = 0.0
        if pos.net_mm == 0.0 or (pos.net_mm > 0) == (q > 0):
            # Opening or adding to the same side → new VWAP, nothing realized.
            new_abs = abs(pos.net_mm) + abs(q)
            pos.avg_price = (pos.avg_price * abs(pos.net_mm)
                             + price * abs(q)) / new_abs
            pos.net_mm += q
        else:
            # Reducing / closing / flipping the position.
            closed = min(abs(pos.net_mm), abs(q))
            direction = 1.0 if pos.net_mm > 0 else -1.0
            realized = (price - pos.avg_price) * closed * direction * 1e4
            pos.net_mm += q
            if abs(q) > closed:                 # flipped past flat
                pos.avg_price = price
            elif pos.net_mm == 0.0:
                pos.avg_price = 0.0
        pos.realized_eur += realized
        return realized

    # ------------------------------------------------------------- reads
    def unrealized_eur(self, marks: dict[str, float]) -> float:
        """Total unrealized P&L given current mid prices keyed by bond code."""
        return sum(p.market_value_pnl(marks.get(p.bond_code, p.avg_price))
                   for p in self.positions.values())

    def total_eur(self, marks: dict[str, float]) -> float:
        return self.realized_eur + self.unrealized_eur(marks)

    def open_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if abs(p.net_mm) > 1e-9]

    def stats(self) -> dict:
        """Scoreboard: counts, hit rate on edge, average edge per ticket."""
        n = len(self.trades)
        good = sum(1 for t in self.trades if t.edge_eur > 0)
        avg_edge = (sum(t.edge_eur for t in self.trades) / n) if n else 0.0
        return {
            "n_trades": n,
            "good_edge": good,
            "edge_hit_rate": (good / n) if n else 0.0,
            "avg_edge_eur": avg_edge,
        }


__all__ = [
    "DealerQuote", "RfqResult", "generate_quotes", "DEALERS",
    "Trade", "Position", "Blotter",
]
