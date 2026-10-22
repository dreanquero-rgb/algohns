"""Tests for the Trading Simulation engine and blotter.

No Streamlit and no threads here: the world's :meth:`tick` and the whole RFQ /
blotter layer are pure given an RNG, so this exercises the maths that the live
page merely renders — bond pricing, dealer-quote shape, position netting, and
the realized/unrealized P&L split that updates on every fill.
"""
from __future__ import annotations

import numpy as np

from algohns.modules import rate_world as rw
from algohns.modules.rfq_sim import Blotter, generate_quotes


# --------------------------------------------------------------- bond maths
def test_price_from_yield_par_when_yield_equals_coupon():
    # A bond priced at its own coupon rate trades at par.
    p = rw.price_from_yield(coupon_rate=0.04, ttm_years=10, ytm=0.04, freq=1)
    assert abs(p - 100.0) < 1e-6


def test_price_falls_as_yield_rises():
    lo = rw.price_from_yield(0.03, 10, 0.03, 1)
    hi = rw.price_from_yield(0.03, 10, 0.05, 1)
    assert hi < lo                       # yields up → price down
    assert rw.price_dv01(0.03, 10, 0.03, 1) > 0   # DV01 positive


def test_price_handles_negative_yield():
    p = rw.price_from_yield(0.0, 10, -0.005, 1)
    assert p > 100.0                     # zero-coupon bond above par at -0.5%


# --------------------------------------------------------------- world tick
def test_world_tick_moves_yields_and_is_deterministic():
    a = rw.RateWorld(seed=7)
    b = rw.RateWorld(seed=7)
    for _ in range(50):
        a.tick()
        b.tick()
    snap_a, snap_b = a.snapshot(), b.snapshot()
    ya = {r["code"]: r["yield"] for r in snap_a["rows"]}
    yb = {r["code"]: r["yield"] for r in snap_b["rows"]}
    assert ya == yb                      # same seed → same world
    # Something actually moved off the opening level.
    moved = any(abs(r["chg_session_bp"]) > 1e-6 for r in snap_a["rows"])
    assert moved


def test_events_eventually_fire_and_hit_the_tape():
    w = rw.RateWorld(seed=3, difficulty="Volatile")
    for _ in range(2000):
        w.tick()
    snap = w.snapshot()
    assert len(snap["events"]) > 0       # geopolitics happened
    # Bund (safe haven, beta < 0) and BTP (periphery, beta > 0) should diverge
    # over a long volatile run: the periphery spread is not pinned at zero.
    spread = {r["code"]: r["spread_vs_bund_bp"] for r in snap["rows"]}
    assert spread["DE"] == 0.0
    assert abs(spread["IT"]) > 0.0


# --------------------------------------------------------------- RFQ panel
def test_generate_quotes_has_two_sided_live_market():
    rng = np.random.default_rng(1)
    res = generate_quotes(
        bond_code="IT", bond_name="Italy (BTP)", fair_price=99.5,
        region="Periphery EU", size_mm=2.0, difficulty="Normale",
        rng=rng, n_dealers=5,
    )
    assert len(res.live_quotes) >= 2
    assert res.best_bid is not None and res.best_ask is not None
    # A sane market: best bid below best ask.
    assert res.best_bid.bid < res.best_ask.ask


# --------------------------------------------------------------- blotter P&L
def test_blotter_books_realized_pnl_on_close():
    bl = Blotter()
    # Lift (buy) 5mm at 100.00, then hit (sell) 5mm at 100.50 → +0.50 pts.
    bl.fill(bond_code="BTP", bond_name="Italy", side="LIFT", price=100.0,
            notional_mm=5.0, fair_price=100.0, ts=0.0)
    bl.fill(bond_code="BTP", bond_name="Italy", side="HIT", price=100.5,
            notional_mm=-5.0, fair_price=100.5, ts=1.0)
    # 0.50 price points on 5mm face = 0.50/100 * 5_000_000 = 25_000 EUR.
    assert abs(bl.realized_eur - 25_000.0) < 1e-6
    assert not bl.open_positions()       # flat again


def test_blotter_short_then_cover_and_live_mark():
    bl = Blotter()
    # Hit (sell/short) 3mm at 101.00.
    bl.fill(bond_code="BUND", bond_name="Germany", side="HIT", price=101.0,
            notional_mm=-3.0, fair_price=101.0, ts=0.0)
    # Still open: mark to a lower price → short is in profit.
    unreal = bl.unrealized_eur({"BUND": 100.0})
    assert unreal > 0.0                  # sold at 101, worth 100 now
    # Cover at 100.00 → realized +1.00 pt on 3mm = 30_000 EUR.
    bl.fill(bond_code="BUND", bond_name="Germany", side="LIFT", price=100.0,
            notional_mm=3.0, fair_price=100.0, ts=1.0)
    assert abs(bl.realized_eur - 30_000.0) < 1e-6


def test_edge_vs_fair_sign():
    bl = Blotter()
    # Lift below fair (bought cheap) → positive edge.
    t = bl.fill(bond_code="X", bond_name="x", side="LIFT", price=99.0,
                notional_mm=1.0, fair_price=99.2, ts=0.0)
    assert t.edge_eur > 0.0
    # Hit below fair (sold cheap) → negative edge.
    t2 = bl.fill(bond_code="Y", bond_name="y", side="HIT", price=99.0,
                 notional_mm=-1.0, fair_price=99.2, ts=0.0)
    assert t2.edge_eur < 0.0
