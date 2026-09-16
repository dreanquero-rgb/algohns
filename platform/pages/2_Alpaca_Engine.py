"""Module 2 UI — Alpaca paper execution and async rebalancing."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import ConfigError, settings  # noqa: E402
from modules.alpaca_execution import (  # noqa: E402
    AlpacaConnector,
    LiveTradingBlocked,
    OrderIntent,
    OrderSide,
    OrderType,
    PAPER_HOSTS,
)
from workers.scheduler import active_backend, list_jobs  # noqa: E402

st.set_page_config(page_title="Alpaca Engine", page_icon="◆", layout="wide")
st.title("Module 2 — Alpaca Asynchronous Execution Engine")

st.error(
    f"**Paper only.** Il client accetta solo {', '.join(sorted(PAPER_HOSTS))}. "
    "Puntare `ALPACA_BASE_URL` all'endpoint live non abilita il trading reale: "
    "solleva `LiveTradingBlocked` alla costruzione del client."
)

if not settings.has_alpaca:
    st.warning(
        "Credenziali Alpaca assenti. Imposta `ALPACA_API_KEY` e "
        "`ALPACA_SECRET_KEY` (vedi `.env.example`). "
        "Il costruttore di ordini sotto funziona comunque in dry-run."
    )

st.caption(f"Backend asincrono attivo: **{active_backend()}**")


@st.cache_resource
def connector() -> AlpacaConnector | None:
    try:
        return AlpacaConnector()
    except (ConfigError, LiveTradingBlocked) as exc:
        st.session_state["conn_error"] = str(exc)
        return None


conn = connector()
tab1, tab2, tab3, tab4 = st.tabs(
    ["Portafoglio", "Costruttore ordini", "Ribilanciamento", "Scheduler"]
)

with tab1:
    if conn is None:
        st.info(st.session_state.get("conn_error", "Connettore non disponibile."))
    else:
        if st.button("Sincronizza", type="primary"):
            st.cache_data.clear()
        try:
            snap = conn.snapshot()
        except Exception as exc:
            st.error(f"Lettura conto fallita: {exc}")
        else:
            a, b, c, d = st.columns(4)
            a.metric("Equity", f"${snap.equity:,.2f}")
            b.metric("Liquidità", f"${snap.cash:,.2f}")
            c.metric("Buying power", f"${snap.buying_power:,.2f}")
            d.metric(
                "Concentrazione (HHI)", f"{snap.concentration:.3f}",
                help="1.0 = monotitolo. Sopra 0.25 la diversificazione è nominale.",
            )
            if snap.trading_blocked:
                st.error("Il conto ha il trading bloccato lato Alpaca.")
            if snap.positions:
                st.dataframe(
                    pd.DataFrame([{
                        "Ticker": p.symbol, "Qtà": p.qty,
                        "Prezzo medio": p.avg_entry_price,
                        "Prezzo attuale": p.current_price,
                        "Valore": p.market_value, "P&L": p.unrealized_pl,
                        "P&L %": p.unrealized_plpc * 100,
                        "Peso %": snap.weights.get(p.symbol, 0.0) * 100,
                    } for p in snap.positions]).set_index("Ticker"),
                    width='stretch',
                )
            else:
                st.info("Nessuna posizione aperta.")

with tab2:
    st.subheader("Costruttore ordini")
    st.caption(
        "Ogni ordine è validato prima dell'invio: sizing esclusivo qty/notional, "
        "prezzi obbligatori per limit e stop, frazionari solo market+day."
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        symbol = st.text_input("Ticker", "AAPL").upper()
        side = st.radio("Lato", list(OrderSide), format_func=lambda s: s.value.upper())
    with c2:
        sizing = st.radio("Dimensionamento", ["Notional ($)", "Quantità"])
        if sizing.startswith("Notional"):
            notional = st.number_input("Notional $", 1.0, 1e7, 1000.0, step=100.0)
            qty = None
        else:
            qty = st.number_input("Quantità", 0.0001, 1e6, 10.0, step=1.0)
            notional = None
    with c3:
        otype = st.selectbox("Tipo", list(OrderType), format_func=lambda o: o.value)
        limit_price = (
            st.number_input("Limit price", 0.01, 1e6, 100.0)
            if otype in (OrderType.LIMIT, OrderType.STOP_LIMIT) else None
        )
        stop_price = (
            st.number_input("Stop price", 0.01, 1e6, 95.0)
            if otype in (OrderType.STOP, OrderType.STOP_LIMIT) else None
        )

    try:
        intent = OrderIntent(
            symbol=symbol, side=side, qty=qty, notional=notional,
            order_type=otype, limit_price=limit_price, stop_price=stop_price,
        )
    except ValueError as exc:
        st.error(f"Ordine non valido: {exc}")
    else:
        st.code(intent.describe(), language=None)
        d1, d2 = st.columns(2)
        if d1.button("Dry run (nessun invio)"):
            st.success(f"Validato: {intent.describe()}")
        if d2.button("Invia a paper", type="primary", disabled=conn is None):
            res = conn.submit(intent)
            (st.success if res.accepted else st.error)(res.summary)

with tab3:
    st.subheader("Ribilanciamento verso pesi target")
    st.caption(
        "Le vendite precedono gli acquisti così che il ricavato finanzi il "
        "lato buy. La liquidazione a zero usa la quantità, non il notional, "
        "per non lasciare residui."
    )
    raw = st.text_area(
        "Pesi target (TICKER=peso per riga)",
        "AAPL=0.25\nMSFT=0.25\nJNJ=0.20\nXOM=0.15\nJPM=0.15",
        height=140,
    )
    targets: dict[str, float] = {}
    errors: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            errors.append(f"riga non valida: {line!r}")
            continue
        k, _, v = line.partition("=")
        try:
            targets[k.strip().upper()] = float(v)
        except ValueError:
            errors.append(f"peso non numerico: {line!r}")

    for e in errors:
        st.warning(e)
    total = sum(targets.values())
    st.caption(f"Somma pesi: **{total:.4f}** — il resto resta liquidità.")

    min_drift = st.slider("Soglia di drift minima", 0.0, 0.05, 0.005, 0.001,
                          format="%.3f")
    if st.button("Calcola piano", disabled=conn is None or not targets):
        try:
            plan = conn.build_rebalance(targets, min_drift=min_drift)
        except ValueError as exc:
            st.error(f"Piano non calcolabile: {exc}")
        else:
            st.metric("Turnover", f"{plan.turnover:.2%}")
            if plan.is_empty:
                st.success("Portafoglio già in linea: nessun ordine necessario.")
            else:
                for line in plan.describe():
                    st.code(line, language=None)
            if plan.skipped:
                with st.expander(f"Titoli saltati ({len(plan.skipped)})"):
                    for k, why in plan.skipped.items():
                        st.caption(f"**{k}** — {why}")
            st.dataframe(
                pd.DataFrame({"Drift": plan.drifts}).style.format("{:+.3%}"),
                width='stretch',
            )
            st.info(
                "L'esecuzione via job richiede il token di conferma "
                "`EXECUTE`: un tick dello scheduler o un retry non possono "
                "operare da soli."
            )

with tab4:
    st.subheader("Job schedulati")
    st.caption(
        "APScheduler gira in-process: i job muoiono col processo e non hanno "
        "storico di retry. Celery serve quando questo conta."
    )
    jobs = list_jobs()
    if jobs:
        st.dataframe(pd.DataFrame(jobs), width='stretch')
    else:
        st.info("Nessun job registrato in questa sessione.")
    st.divider()
    st.subheader("Kill switch")
    st.caption("Annulla tutti gli ordini aperti e liquida tutte le posizioni.")
    if st.checkbox("Confermo di voler liquidare tutto (paper)"):
        if st.button("ESEGUI KILL SWITCH", type="primary", disabled=conn is None):
            cancelled = conn.cancel_all()
            closed = conn.close_all_positions()
            st.warning(f"{cancelled} ordini annullati, {closed} posizioni chiuse.")
