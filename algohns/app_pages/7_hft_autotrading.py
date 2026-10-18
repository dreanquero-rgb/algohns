"""② Auto-Trading · HFT — statistical intraday strategies on 1-minute bars.

A section of its own, fully separate from Portfolio Management: its strategies
live under section ``HFT`` in the strategy store and never show up in PM. The
engine here reacts to recent 1-minute bars and trades a dedicated Alpaca paper
account. For now it runs *while the app is open* — see the footer for how to run
it 24/7 on a free Oracle VM.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from algohns import charts as ch
from algohns.config import get_settings
from algohns.core import strategy_store as store
from algohns.core.data_providers import get_market_data
from algohns.core.persistence import load_state, save_state
from algohns.modules import hft_strategies as hft
from algohns.modules.alpaca_execution import AlpacaExecutionEngine, OrderTicket
from algohns.ui import header

SECTION = "HFT"

header(
    "Auto-Trading · HFT",
    "Statistico — strategie intraday su barre a 1 minuto, conto Alpaca dedicato.",
    "② HFT",
)

settings = get_settings()


def _close_series(df: pd.DataFrame, symbol: str) -> pd.Series:
    """Pull one symbol's close series out of a MarketData.history frame."""
    if df is None or df.empty:
        return pd.Series(dtype=float)
    if symbol in df.columns:
        s = df[symbol]
    else:
        s = df.iloc[:, 0]
    return pd.to_numeric(s, errors="coerce").dropna()


tab_lib, tab_run, tab_risk = st.tabs(
    ["📚 Strategie", "▶ Esegui / Monitor", "🎯 Rischio (statistico)"]
)

# ---------------------------------------------------------------------------
# Strategy library (section-scoped: HFT only)
# ---------------------------------------------------------------------------
with tab_lib:
    st.subheader("Nuova strategia HFT")
    kinds = list(hft.HFT_STRATEGIES)
    kind = st.selectbox(
        "Tipo", kinds, format_func=lambda k: hft.HFT_STRATEGIES[k]["label"])
    name = st.text_input("Nome", value=f"{hft.HFT_STRATEGIES[kind]['label']} 1m")
    symbols_raw = st.text_input(
        "Simboli (azioni/ETF USA, separati da virgola)", value="AAPL, MSFT, SPY")

    st.markdown("**Parametri**")
    schema = hft.HFT_STRATEGIES[kind]["params"]
    params: dict = {}
    pcols = st.columns(max(1, len(schema)))
    for i, (pname, pdefault) in enumerate(schema.items()):
        if isinstance(pdefault, int):
            params[pname] = pcols[i].number_input(
                pname, value=int(pdefault), step=1, min_value=1, key=f"new_{kind}_{pname}")
        else:
            params[pname] = pcols[i].number_input(
                pname, value=float(pdefault), step=0.001, format="%.4f",
                key=f"new_{kind}_{pname}")

    if st.button("💾 Salva strategia", type="primary"):
        syms = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
        if not name.strip():
            st.error("Dai un nome alla strategia.")
        elif not syms:
            st.error("Inserisci almeno un simbolo.")
        else:
            strat = store.Strategy(
                name=name.strip(), section=SECTION, kind="builtin",
                params={"builtin": kind, "symbols": syms, **params},
            )
            store.save_strategy(strat)
            st.success(f"Salvata «{strat.name}». È nella libreria HFT (solo qui).")
            st.rerun()

    st.divider()
    st.subheader("Libreria HFT")
    saved = store.list_strategies(SECTION)
    if not saved:
        st.info("Nessuna strategia HFT salvata. Creane una qui sopra.")
    for s in saved:
        with st.container(border=True):
            top = st.columns([4, 1, 1])
            builtin = s.params.get("builtin", "?")
            syms = ", ".join(s.params.get("symbols", []))
            top[0].markdown(
                f"**{s.name}**  \n`{builtin}` · {syms}  \n"
                f"<small>{', '.join(f'{k}={v}' for k, v in s.params.items() if k not in ('builtin','symbols'))}</small>",
                unsafe_allow_html=True)
            new_en = top[1].toggle("Attiva", value=s.enabled, key=f"en_{s.id}")
            if new_en != s.enabled:
                store.set_enabled(SECTION, s.id, new_en)
                st.rerun()
            if top[2].button("🗑", key=f"del_{s.id}", help="Elimina"):
                store.delete_strategy(SECTION, s.id)
                st.rerun()

# ---------------------------------------------------------------------------
# Run / monitor
# ---------------------------------------------------------------------------
with tab_run:
    with st.expander("🏦 Verifica conto HFT", expanded=False):
        st.caption("Conto HFT = chiavi principali `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`. "
                   "Dev'essere **diverso** dal numero di conto del Portfolio Management.")
        if st.button("Mostra conto HFT", disabled=not settings.alpaca_configured):
            try:
                snap = AlpacaExecutionEngine().portfolio_snapshot()
                st.success(f"N° conto HFT: `{snap.get('account_number')}` · "
                           f"equity ${snap.get('equity', 0):,.2f}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Errore Alpaca: {exc}")
        if not settings.alpaca_configured:
            st.info("Chiavi principali Alpaca non configurate.")

    saved = store.list_strategies(SECTION)
    if not saved:
        st.info("Prima salva una strategia nella tab **Strategie**.")
    else:
        by_id = {s.id: s for s in saved}
        pick_id = st.selectbox(
            "Strategia", list(by_id),
            format_func=lambda i: f"{by_id[i].name}  ·  {by_id[i].params.get('builtin')}")
        pick = by_id[pick_id]
        syms = pick.params.get("symbols", [])
        builtin = pick.params.get("builtin")
        run_params = {k: v for k, v in pick.params.items()
                      if k not in ("builtin", "symbols")}
        st.caption(f"Simboli: {', '.join(syms)} · parametri: {run_params}")

        if st.button("📥 Scarica barre 1m & calcola segnali"):
            try:
                md = get_market_data()
                raw = md.history(syms, period="5d", interval="1m")
                rows = []
                sig_map = {}
                for sym in syms:
                    close = _close_series(raw, sym)
                    if close.empty:
                        rows.append({"symbol": sym, "signal": "n/d", "last": None})
                        continue
                    sig = hft.compute_signals(builtin, close, run_params)
                    last = int(sig.iloc[-1]) if len(sig) else 0
                    sig_map[sym] = (close, sig)
                    rows.append({
                        "symbol": sym,
                        "signal": {1: "BUY ▲", -1: "SELL ▼", 0: "FLAT"}[last],
                        "last_price": round(float(close.iloc[-1]), 2),
                    })
                st.session_state["_hft_rows"] = rows
                st.session_state["_hft_sig"] = sig_map
            except Exception as exc:  # noqa: BLE001
                st.error(f"Dati non disponibili: {exc}")

        rows = st.session_state.get("_hft_rows")
        sig_map = st.session_state.get("_hft_sig", {})
        if rows:
            st.markdown("**Segnali sull'ultima barra**")
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            focus = st.selectbox("Grafico simbolo", [r["symbol"] for r in rows])
            if focus in sig_map:
                close, sig = sig_map[focus]
                st.plotly_chart(
                    ch.line(close.rename(focus).to_frame(),
                            title=f"{focus} — prezzo 1m"),
                    width="stretch")
                bt = hft.quick_backtest(close, sig)
                m = st.columns(3)
                m[0].metric("Return (toy)", f"{bt['total_return']*100:+.2f}%")
                m[1].metric("Sharpe (toy)", f"{bt['sharpe_annualised']:.2f}")
                m[2].metric("Trade", f"{bt['trades']}")
                st.plotly_chart(
                    ch.line(bt["equity"].rename("Equity (segnale)").to_frame(),
                            title="Backtest giocattolo (no costi/slippage)"),
                    width="stretch")
                st.caption("⚠️ Backtest indicativo: nessun costo, slippage o fill reale.")

            st.divider()
            st.markdown("**Esecuzione paper** (solo acquisti, notional)")
            st.caption("🤖 Conto **HFT** = chiavi principali `ALPACA_API_KEY` / "
                       "`ALPACA_SECRET_KEY` (il Portfolio Management usa le sue "
                       "`ALPACA_PM_*`, se impostate).")
            notional = st.number_input("$ per posizione (BUY)", value=1000.0,
                                       step=100.0, min_value=10.0)
            confirm = st.checkbox("Confermo l'invio di ordini paper su Alpaca")
            buys = [r["symbol"] for r in rows if r.get("signal") == "BUY ▲"]
            st.caption(f"Ordini di acquisto proposti: {', '.join(buys) or '— nessuno'}")
            if st.button("🚀 Invia ordini BUY (paper)",
                         disabled=not (confirm and settings.alpaca_configured and buys)):
                try:
                    engine = AlpacaExecutionEngine()
                    results = []
                    for sym in buys:
                        tk = OrderTicket(symbol=sym, notional=float(notional), side="buy")
                        results.append(engine.submit_order(tk))
                    st.success(f"Inviati {len(results)} ordini paper.")
                    st.json(results)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Errore invio ordini: {exc}")
            if not settings.alpaca_configured:
                st.info("Chiavi Alpaca non configurate: imposta ALPACA_API_KEY / "
                        "ALPACA_SECRET_KEY per l'esecuzione.")

# ---------------------------------------------------------------------------
# Statistical risk profile (distinct from PM's macro profile)
# ---------------------------------------------------------------------------
with tab_risk:
    st.subheader("Profilo di rischio — statistico")
    st.caption("Diverso da Portfolio Management (macro): qui ragioni in termini "
               "di volatilità, esposizione e stop, non di orizzonte/allocazione.")
    saved_risk = load_state("risk_HFT") or {}
    c = st.columns(2)
    vol_target = c[0].number_input("Volatilità target annua (%)",
                                   value=float(saved_risk.get("vol_target", 15.0)),
                                   step=1.0, min_value=1.0)
    max_pos = c[1].number_input("Max posizioni contemporanee",
                                value=int(saved_risk.get("max_positions", 5)),
                                step=1, min_value=1)
    stop = c[0].number_input("Stop per trade (%)",
                             value=float(saved_risk.get("stop_pct", 1.0)),
                             step=0.1, min_value=0.1)
    gross = c[1].number_input("Esposizione lorda max (% equity)",
                              value=float(saved_risk.get("max_gross", 100.0)),
                              step=5.0, min_value=1.0)
    if st.button("💾 Salva profilo statistico", type="primary"):
        save_state("risk_HFT", {
            "vol_target": vol_target, "max_positions": int(max_pos),
            "stop_pct": stop, "max_gross": gross,
        })
        st.success("Profilo statistico salvato.")

# ---------------------------------------------------------------------------
# Footer — how to run 24/7
# ---------------------------------------------------------------------------
st.divider()
st.caption(
    "⏱️ **Esecuzione continua (24/7):** il worker dedicato "
    "(`python -m algohns.workers.runner`) esegue le strategie HFT **abilitate** "
    "qui sopra, sempre, anche a browser chiuso. Gira sulla **VM Oracle "
    "Always-Free** insieme all'app (stesso strategy store). Setup completo in "
    "`docs/ORACLE_SETUP.md`; il worker invia ordini solo con "
    "`ALGO_WORKER_ENABLED=true`."
)
