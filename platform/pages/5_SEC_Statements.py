"""Module 5 UI — Consolidated SEC financial statements aggregator."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import ConfigError, settings  # noqa: E402
from modules.sec_aggregator import SecAggregator, Statement  # noqa: E402

st.set_page_config(page_title="SEC Statements", page_icon="◆", layout="wide")
st.title("Module 5 — Consolidated SEC Financial Statements Aggregator")
st.caption(
    "XBRL companyfacts normalizzato. Ogni voce dichiara una catena di tag "
    "alternativi, perché US-GAAP consente di esprimere lo stesso concetto con "
    "elementi diversi a seconda del filer e dell'anno."
)

try:
    settings.require_sec_user_agent()
except ConfigError as exc:
    st.error(str(exc))
    st.info(
        "EDGAR impone un User-Agent con contatto reale. Imposta "
        "`SEC_USER_AGENT` in `platform/.env`, es. "
        "`algohns-research/1.0 (tua.email@dominio.it)`."
    )
    st.stop()


@st.cache_resource
def aggregator() -> SecAggregator:
    return SecAggregator()


@st.cache_data(ttl=86400, show_spinner="Interrogo EDGAR…")
def _statement(ticker: str, statement: str, years: int):
    d = aggregator().statement(ticker, Statement(statement), years=years)
    return (
        d.to_frame(), d.tag_provenance(), d.derived_ratios(),
        d.missing_items, d.warnings, d.company_name,
    )


@st.cache_data(ttl=86400, show_spinner="Confronto i bilanci…")
def _compare(tickers: tuple[str, ...], statement: str):
    frame = aggregator().compare(list(tickers), Statement(statement))
    return frame, frame.attrs.get("fiscal_year"), frame.attrs.get("errors", {})


@st.cache_data(ttl=86400, show_spinner="Calcolo i rapporti…")
def _ratios(tickers: tuple[str, ...]):
    return aggregator().compare_ratios(list(tickers))


tab_single, tab_compare, tab_ratios = st.tabs(
    ["Singolo emittente", "Confronto affiancato", "Rapporti"]
)

with tab_single:
    c1, c2, c3 = st.columns([1, 1, 1])
    ticker = c1.text_input("Ticker", "AAPL").upper()
    statement = c2.selectbox(
        "Prospetto", [s.value for s in Statement],
        format_func=lambda s: {
            "income_statement": "Conto economico",
            "balance_sheet": "Stato patrimoniale",
            "cash_flow": "Rendiconto finanziario",
        }[s],
    )
    years = c3.slider("Esercizi", 2, 10, 5)

    if st.button("Carica", type="primary"):
        try:
            frame, prov, ratios, missing, warns, name = _statement(
                ticker, statement, years
            )
        except (KeyError, RuntimeError) as exc:
            st.error(str(exc))
        else:
            st.subheader(name)
            for w in warns:
                st.warning(w)
            if frame.empty:
                st.info("Nessun dato normalizzabile per questo prospetto.")
            else:
                st.dataframe(
                    frame.style.format("{:,.1f}", na_rep="—"),
                    width='stretch',
                )
                st.caption(
                    "Valori in milioni di USD; EPS e numero azioni non sono "
                    "riscalati."
                )
                if ratios:
                    st.subheader("Rapporti derivati")
                    st.dataframe(
                        pd.DataFrame(ratios).style.format("{:,.2f}", na_rep="—"),
                        width='stretch',
                    )
                with st.expander("Provenienza dei tag XBRL"):
                    st.caption(
                        "Quale elemento ha prodotto ogni valore. Serve quando "
                        "un numero sorprende: si risale all'elemento."
                    )
                    st.dataframe(prov, width='stretch')
                if missing:
                    st.caption(
                        "Voci non mappate: " + ", ".join(missing) +
                        " — possono mancare dal bilancio o usare tag non in catalogo."
                    )

with tab_compare:
    st.caption(
        "Il confronto sceglie l'ultimo esercizio **comune** a tutti i ticker, "
        "così la tabella non mette a fianco un FY2025 e un FY2023."
    )
    raw = st.text_input("Ticker separati da virgola", "AAPL, MSFT, NVDA, JNJ")
    statement_c = st.selectbox(
        "Prospetto da confrontare", [s.value for s in Statement], key="cmp",
        format_func=lambda s: {
            "income_statement": "Conto economico",
            "balance_sheet": "Stato patrimoniale",
            "cash_flow": "Rendiconto finanziario",
        }[s],
    )
    if st.button("Confronta", type="primary"):
        tickers = tuple(t.strip().upper() for t in raw.split(",") if t.strip())
        if len(tickers) < 2:
            st.warning("Serve almeno due ticker.")
        else:
            try:
                frame, fy, errors = _compare(tickers, statement_c)
            except RuntimeError as exc:
                st.error(str(exc))
            else:
                st.subheader(f"Esercizio {fy}")
                st.dataframe(
                    frame.style.format("{:,.1f}", na_rep="—"),
                    width='stretch', height=500,
                )
                st.caption("Milioni di USD. Le celle vuote sono voci non riportate.")
                for t, err in errors.items():
                    st.warning(f"**{t}** — {err}")

with tab_ratios:
    st.caption(
        "I rapporti attraversano i tre prospetti: il ROE vuole il patrimonio, "
        "il free cash flow vuole il capex. Vengono uniti prima del calcolo."
    )
    raw_r = st.text_input("Ticker separati da virgola", "AAPL, MSFT, NVDA, JNJ",
                          key="ratios_in")
    if st.button("Calcola rapporti", type="primary"):
        tickers = tuple(t.strip().upper() for t in raw_r.split(",") if t.strip())
        if not tickers:
            st.warning("Inserisci almeno un ticker.")
        else:
            try:
                table = _ratios(tickers)
            except RuntimeError as exc:
                st.error(str(exc))
            else:
                st.dataframe(
                    table.style.format("{:,.2f}", na_rep="—"),
                    width='stretch', height=440,
                )
                for metric in ("Net Margin %", "ROE %", "Gross Margin %"):
                    if metric in table.index:
                        st.bar_chart(table.loc[metric], height=240)
                        st.caption(metric)
                        break
