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


# --------------------------------------------------------------- curve shape
def test_base_curve_upward_sloping_for_bund():
    de = next(s for s in rw.UNIVERSE if s.code == "DE")
    curve = de.base_curve()
    assert curve[2] < curve[5] < curve[10] < curve[30]   # normal, upward


def test_curve_shapes_target_front_vs_long():
    short, long = rw.CURVE_SHAPES["short"], rw.CURVE_SHAPES["long"]
    assert short[2] > short[10]      # policy shock bites the front end
    assert long[30] > long[2]        # fiscal/supply shock bites the long end


def test_snapshot_rows_carry_full_curve_and_slope():
    w = rw.RateWorld(seed=1)
    for _ in range(20):
        w.tick()
    row = {r["code"]: r for r in w.snapshot()["rows"]}["IT"]
    assert set(row["curve"]) == set(rw.TENORS)
    # slope_2s10s = (10y - 2y) in bp, matches the curve points.
    expected = (row["curve"][10]["yield"] - row["curve"][2]["yield"]) * 100
    assert abs(row["slope_2s10s_bp"] - expected) < 1e-6


def test_instrument_and_marks_cover_every_point():
    w = rw.RateWorld(seed=2)
    for _ in range(10):
        w.tick()
    inst = w.instrument("IT", 30)
    assert inst["code"] == "IT_30y" and inst["tenor"] == 30
    assert inst["price"] > 0 and len(inst["history"]) > 0
    marks = w.marks()
    assert len(marks) == len(rw.UNIVERSE) * len(rw.TENORS)
    assert "IT_30y" in marks and "DE_2y" in marks


def test_dv01_is_on_a_sane_money_scale():
    # Guards the percent→decimal conversion: a 10y benchmark must have a money
    # DV01 of roughly a few hundred to a couple thousand EUR per 1mm, and the
    # 30y must out-DV01 the 2y. A missed /100 collapses these to ~tens of EUR.
    w = rw.RateWorld(seed=4)
    it10 = w.instrument("IT", 10)
    money_dv01 = it10["dv01"] * 1e4            # EUR per bp per 1mm face
    assert 400 < money_dv01 < 2500
    assert w.instrument("IT", 30)["dv01"] > w.instrument("IT", 2)["dv01"]


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
