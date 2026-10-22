"""Streamlit page — Trading Simulation (live sovereign-bond RFQ desk).

A practice trading floor. A background world (:mod:`algohns.modules.rate_world`)
moves ~15 sovereign benchmark yields in real time and fires geopolitical shocks
that ripple through correlated clusters; the trader watches the tape, fires an
RFQ on a bond, reads the dealer panel, and does **hit / lift / pass** before the
quote fades and the market moves away. The **trader P&L updates on every
transaction** and keeps marking live between them.

Why the split into fragments: Streamlit re-runs the whole script on every click,
but a trading floor must keep moving on its own. So the world ticks on its own
thread, and two ``st.fragment(run_every=...)`` islands redraw the live board and
the RFQ desk on a timer without touching the rest of the page. The world itself
lives in ``st.cache_resource`` so it survives those re-runs — exactly one world
per session.

Paper / practice only. Yield levels are indicative model assumptions, dealers
are simulated, and nothing trades real money.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import streamlit as st

from algohns.modules.rate_world import RateWorld
from algohns.modules.rfq_sim import Blotter, generate_quotes
from algohns.ui import GREEN, RED, code_panel, header, paper_lock_banner

header(
    "Trading Simulation",
    "Live sovereign-bond RFQ desk: the world moves, geopolitics strikes, "
    "you hit / lift / pass before the quote fades.",
    badge="Simulation",
)
paper_lock_banner()
st.caption(
    "Simulatore per esercitarsi. Livelli indicativi (ipotesi di modello, non "
    "dati di mercato), dealer simulati, solo paper."
)

QUOTE_LIFE = 5.0           # seconds a dealer panel stays firm before it fades
BUND_HELP = "Verde = rendimento in calo (prezzo su). Rosso = rendimento in salita."


# ---------------------------------------------------------------------------
# World singleton (survives Streamlit re-runs) + per-session trader state
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _world(seed: int) -> RateWorld:
    """One live world per seed, already ticking on its background thread."""
    return RateWorld(seed=seed).start()


ss = st.session_state
ss.setdefault("ts_seed", 7)
ss.setdefault("ts_blotter", Blotter())
ss.setdefault("ts_qrng", np.random.default_rng(12345))
ss.setdefault("ts_rfq", None)           # active RFQ: dict or None
ss.setdefault("ts_flash", None)         # last-fill confirmation message

world = _world(ss["ts_seed"])


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### ⚡ Desk controls")
    difficulty = st.select_slider(
        "Regime", options=["Calmo", "Normale", "Volatile"], value="Normale",
        help="Scala volatilità e frequenza degli eventi geopolitici.",
    )
    size_mm = st.number_input("Size RFQ (mln nominali)", min_value=0.5,
                              max_value=50.0, value=2.0, step=0.5)
    n_dealers = st.slider("Dealer interpellati", 2, 6, 5)
    paused = st.toggle("⏸ Pausa mercato", value=False)
    cols = st.columns(2)
    if cols[0].button("🔄 Reset mondo", use_container_width=True):
        world.stop()
        _world.clear()
        ss["ts_seed"] = int(ss["ts_seed"]) + 1
        ss["ts_rfq"] = None
        ss["ts_flash"] = None
        st.rerun()
    if cols[1].button("🧹 Reset book", use_container_width=True):
        ss["ts_blotter"] = Blotter()
        ss["ts_rfq"] = None
        ss["ts_flash"] = None
        st.rerun()

world.set_difficulty(difficulty)
world.paused = bool(paused)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _eur(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}€{abs(x):,.0f}"


def _tint(value: float, *, up_is_good_green: bool = True) -> str:
    """Color for a signed number. For yields, down (negative) = green."""
    if abs(value) < 1e-9:
        return "#8A9099"
    good = (value < 0) if up_is_good_green else (value > 0)
    return GREEN if good else RED


# ---------------------------------------------------------------------------
# Live board: P&L banner + market tape + event ticker (auto-refreshing)
# ---------------------------------------------------------------------------
@st.fragment(run_every=0.6)
def live_board() -> None:
    snap = world.snapshot()
    rows = snap["rows"]
    marks = {r["code"]: r["price"] for r in rows}
    bl: Blotter = ss["ts_blotter"]

    realized = bl.realized_eur
    unreal = bl.unrealized_eur(marks)
    total = realized + unreal
    stats = bl.stats()

    # ---- P&L banner (updates every transaction, and live in between) ----
    st.markdown(
        f"<div style='font-size:.72rem;letter-spacing:.14em;color:#8A9099;"
        f"text-transform:uppercase'>Trader P&amp;L</div>"
        f"<div style='font-size:2.2rem;font-weight:800;line-height:1.1;"
        f"color:{_tint(total, up_is_good_green=False)}'>{_eur(total)}</div>",
        unsafe_allow_html=True,
    )
    m = st.columns(5)
    m[0].metric("Realizzato", _eur(realized))
    m[1].metric("Non realizzato", _eur(unreal))
    m[2].metric("Trade", stats["n_trades"])
    m[3].metric("Edge+ rate", f"{stats['edge_hit_rate']*100:.0f}%")
    m[4].metric("Regime", snap["difficulty"])

    st.divider()
    left, right = st.columns([3, 2])

    # ---- Market tape, grouped by region so contagion is visible ----
    with left:
        st.markdown("##### 🌍 Market tape")
        st.caption(BUND_HELP)
        order = ["Core EU", "Periphery EU", "Anglo", "Japan",
                 "LatAm EM", "EMEA EM"]
        by_region: dict[str, list] = {k: [] for k in order}
        for r in rows:
            by_region.setdefault(r["region"], []).append(r)
        html = ["<table style='width:100%;border-collapse:collapse;"
                "font-family:ui-monospace,monospace;font-size:.82rem'>"]
        html.append(
            "<tr style='color:#8A9099;text-align:right;font-size:.7rem'>"
            "<th style='text-align:left'>Bond</th><th>Yield</th>"
            "<th>Δ sess.</th><th>vs Bund</th><th>Price</th></tr>")
        for region in order:
            group = by_region.get(region, [])
            if not group:
                continue
            html.append(
                f"<tr><td colspan='5' style='padding-top:8px;color:#8A9099;"
                f"font-size:.68rem;letter-spacing:.1em;text-transform:uppercase'>"
                f"{region}</td></tr>")
            for r in group:
                arrow = "▼" if r["chg_session_bp"] < 0 else (
                    "▲" if r["chg_session_bp"] > 0 else "·")
                ct = _tint(r["chg_session_bp"])
                cp = _tint(r["chg_tick_bp"])
                html.append(
                    "<tr style='border-top:1px solid rgba(255,255,255,.05)'>"
                    f"<td style='text-align:left'>{r['flag']} {r['name']}"
                    f"<span style='color:#5A616B'> · {r['rating']}</span></td>"
                    f"<td style='text-align:right;color:{cp}'>{r['yield']:.2f}%</td>"
                    f"<td style='text-align:right;color:{ct}'>{arrow} "
                    f"{abs(r['chg_session_bp']):.1f}</td>"
                    f"<td style='text-align:right;color:#C9CED6'>"
                    f"{r['spread_vs_bund_bp']:+.0f}</td>"
                    f"<td style='text-align:right;color:#C9CED6'>"
                    f"{r['price']:.2f}</td></tr>")
        html.append("</table>")
        st.markdown("".join(html), unsafe_allow_html=True)

    # ---- Geopolitical event ticker ----
    with right:
        st.markdown("##### 📰 Geopolitics")
        now = time.time()
        events = snap["events"][:8]
        if not events:
            st.caption("Mercato tranquillo… per ora.")
        for e in events:
            col = RED if e["risk"] > 0 else GREEN   # risk-off red, risk-on green
            ago = max(0, int(now - e["ts"]))
            tag = {"global": "GLOBAL", "region": "REGION",
                   "country": "SINGLE"}.get(e["scope"], e["scope"].upper())
            st.markdown(
                f"<div style='border-left:3px solid {col};padding:2px 10px;"
                f"margin-bottom:6px'>"
                f"<span style='color:{col};font-size:.62rem;font-weight:700;"
                f"letter-spacing:.08em'>{tag} · {e['category'].upper()}</span>"
                f"<span style='color:#5A616B;font-size:.62rem'> · {ago}s fa</span>"
                f"<br><span style='color:#E6E9ED;font-size:.82rem'>"
                f"{e['text']}</span></div>",
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# RFQ desk: pick a bond, request quotes, hit / lift / pass (auto-refreshing so
# the countdown ticks and the live mid keeps moving under the panel)
# ---------------------------------------------------------------------------
@st.fragment(run_every=0.5)
def rfq_desk() -> None:
    snap = world.snapshot()
    rows = {r["code"]: r for r in snap["rows"]}
    labels = {r["code"]: f"{r['flag']} {r['name']}" for r in snap["rows"]}
    codes = list(rows)

    st.markdown("##### 🎫 RFQ desk")
    if ss["ts_flash"]:
        st.success(ss["ts_flash"])

    code = st.selectbox("Strumento", codes,
                        format_func=lambda c: labels[c], key="ts_sel")
    live = rows[code]
    c = st.columns(4)
    c[0].metric("Live mid", f"{live['price']:.2f}")
    c[1].metric("Yield", f"{live['yield']:.2f}%")
    c[2].metric("vs Bund", f"{live['spread_vs_bund_bp']:+.0f} bp")
    c[3].metric("DV01 / mln", f"€{live['dv01']*1e4:,.0f}")

    # Live price line of the selected bond — the mid you are chasing.
    cty = world.country(code)
    if len(cty["history"]) > 2:
        st.line_chart(pd.DataFrame({"mid": cty["history"]}), height=130)

    rfq = ss["ts_rfq"]
    # New RFQ, or one on a different bond, or an expired one → request panel.
    if st.button("📨 Request quote", type="primary", use_container_width=True,
                 key="ts_req"):
        res = generate_quotes(
            bond_code=code, bond_name=live["name"], fair_price=live["price"],
            region=live["region"], size_mm=float(ss.get("ts_size_cache", 2.0)),
            difficulty=snap["difficulty"], rng=ss["ts_qrng"],
            n_dealers=int(ss.get("ts_dealers_cache", 5)),
        )
        ss["ts_rfq"] = {"res": res, "ts": time.time(), "code": code}
        ss["ts_flash"] = None
        st.rerun(scope="fragment")

    rfq = ss["ts_rfq"]
    if not rfq or rfq["code"] != code:
        st.caption("Scegli lo strumento e lancia l'RFQ ai dealer.")
        return

    res = rfq["res"]
    remaining = QUOTE_LIFE - (time.time() - rfq["ts"])
    expired = remaining <= 0

    # ---- dealer ladder ----
    bb, ba = res.best_bid, res.best_ask
    st.progress(max(0.0, min(1.0, remaining / QUOTE_LIFE)),
                text=("⏱ quota scaduta — rifai RFQ" if expired
                      else f"⏱ {remaining:.1f}s · firm"))
    lines = ["<table style='width:100%;border-collapse:collapse;"
             "font-family:ui-monospace,monospace;font-size:.82rem'>",
             "<tr style='color:#8A9099;font-size:.7rem'><th style='text-align:left'>"
             "Dealer</th><th style='text-align:right'>Bid</th>"
             "<th style='text-align:right'>Ask</th>"
             "<th style='text-align:right'>Size</th>"
             "<th style='text-align:right'>ms</th></tr>"]
    for q in res.quotes:
        if not q.responded:
            lines.append(
                f"<tr><td style='text-align:left;color:#5A616B'>{q.dealer}</td>"
                "<td colspan='4' style='text-align:right;color:#5A616B'>"
                "no quote</td></tr>")
            continue
        is_bb = bb is not None and q.dealer == bb.dealer
        is_ba = ba is not None and q.dealer == ba.dealer
        bid_c = GREEN if is_bb else "#C9CED6"
        ask_c = RED if is_ba else "#C9CED6"
        lines.append(
            f"<tr style='border-top:1px solid rgba(255,255,255,.05)'>"
            f"<td style='text-align:left;color:#E6E9ED'>{q.dealer}</td>"
            f"<td style='text-align:right;color:{bid_c};font-weight:"
            f"{'800' if is_bb else '400'}'>{q.bid:.2f}</td>"
            f"<td style='text-align:right;color:{ask_c};font-weight:"
            f"{'800' if is_ba else '400'}'>{q.ask:.2f}</td>"
            f"<td style='text-align:right;color:#8A9099'>{q.size_mm:.1f}</td>"
            f"<td style='text-align:right;color:#5A616B'>{q.latency_ms}</td></tr>")
    lines.append("</table>")
    st.markdown("".join(lines), unsafe_allow_html=True)

    # ---- action buttons: lift (buy/long) · hit (sell/short) · pass ----
    a = st.columns(3)
    lift_lbl = f"⬆ LIFT {ba.ask:.2f}\n(compra / long)" if ba else "⬆ LIFT"
    hit_lbl = f"⬇ HIT {bb.bid:.2f}\n(vendi / short)" if bb else "⬇ HIT"
    if a[0].button(lift_lbl, use_container_width=True, disabled=expired or not ba,
                   key="ts_lift"):
        _book(code, "LIFT", ba.ask, +res_size(rfq))
    if a[1].button(hit_lbl, use_container_width=True, disabled=expired or not bb,
                   key="ts_hit"):
        _book(code, "HIT", bb.bid, -res_size(rfq))
    if a[2].button("✋ PASS", use_container_width=True, key="ts_pass"):
        ss["ts_rfq"] = None
        ss["ts_flash"] = "Pass — nessun trade."
        st.rerun(scope="fragment")


def res_size(rfq: dict) -> float:
    """Executed clip = the min size across the two best sides, capped by ask."""
    res = rfq["res"]
    sizes = [q.size_mm for q in res.live_quotes]
    return float(min(sizes)) if sizes else 1.0


def _book(code: str, side: str, price: float, notional_mm: float) -> None:
    """Book a fill against the CURRENT live fair, then refresh everything."""
    bl: Blotter = ss["ts_blotter"]
    fair = world.country(code)["price"]        # world has moved since the RFQ
    trade = bl.fill(bond_code=code, bond_name=world.country(code)["name"],
                    side=side, price=price, notional_mm=notional_mm,
                    fair_price=fair, ts=time.time())
    edge_word = "edge+" if trade.edge_eur >= 0 else "edge−"
    ss["ts_rfq"] = None
    ss["ts_flash"] = (
        f"{side} {abs(notional_mm):.1f}mln {code} @ {price:.2f} · "
        f"{edge_word} {_eur(trade.edge_eur)} vs fair {fair:.2f}")
    st.rerun()                                  # full rerun → P&L updates now


# Cache the sidebar values the RFQ fragment needs (it can't read widgets in
# another scope cleanly), then render the two live islands and the book.
ss["ts_size_cache"] = size_mm
ss["ts_dealers_cache"] = n_dealers

live_board()
st.divider()
desk_col, book_col = st.columns([1, 1])
with desk_col:
    rfq_desk()
with book_col:
    st.markdown("##### 📒 Blotter")
    bl: Blotter = ss["ts_blotter"]
    snap = world.snapshot()
    marks = {r["code"]: r["price"] for r in snap["rows"]}
    open_pos = bl.open_positions()
    if open_pos:
        df = pd.DataFrame([{
            "Bond": p.bond_code,
            "Net (mln)": round(p.net_mm, 2),
            "Avg": round(p.avg_price, 2),
            "Mark": round(marks.get(p.bond_code, p.avg_price), 2),
            "Unreal €": round(p.market_value_pnl(
                marks.get(p.bond_code, p.avg_price)), 0),
        } for p in open_pos])
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        st.caption("Nessuna posizione aperta.")

    if bl.trades:
        st.markdown("###### Ultimi trade")
        hist = pd.DataFrame([{
            "#": t.seq, "Side": t.side, "Bond": t.bond_code,
            "Px": round(t.price, 2), "mln": round(t.notional_mm, 1),
            "Edge €": round(t.edge_eur, 0), "Real €": round(t.realized_eur, 0),
        } for t in reversed(bl.trades[-12:])])
        st.dataframe(hist, hide_index=True, use_container_width=True)

st.divider()
with st.expander("ℹ️ Come funziona / come si gioca"):
    st.markdown(
        "- Il **mondo gira da solo**: ~15 benchmark sovrani si muovono in tempo "
        "reale e gli eventi geopolitici colpiscono interi cluster (quando l'Italia "
        "viene declassata, vedi la periferia allargare tutta insieme).\n"
        "- **LIFT** = compri al best offer (vai long). **HIT** = vendi al best bid "
        "(vai short). **PASS** = nessun dealer ti dà un prezzo migliore del fair, "
        "lasci stare.\n"
        "- Le quote dei dealer sono **ferme per ~5s**: se tardi scadono e il "
        "mercato è già scappato.\n"
        "- Il **P&L del trader** si aggiorna a ogni transazione (realizzato) e "
        "continua a marcare live le posizioni aperte."
    )

code_panel(
    [("rate_world", RateWorld), ("rfq_sim", Blotter)],
    title="Engine source",
    intro="Il motore del mondo e il blotter, pescati live con inspect.",
)
