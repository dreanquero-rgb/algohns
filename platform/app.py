"""Algohns Quant Platform — Streamlit orchestrator.

Run from the `platform/` directory:

    streamlit run app.py

Pages live in `pages/` and Streamlit discovers them automatically. This file
holds the shared setup every page relies on: import path, page config,
cached loaders and the status sidebar.

Caching policy: `@st.cache_data` wraps anything that hits the network or
costs real compute, because Streamlit re-runs the whole script on every
widget interaction. Without it, moving a slider re-downloads EDGAR.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

# Make `core`, `modules` and `workers` importable when Streamlit runs this
# file directly rather than as a package.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import ConfigError, settings  # noqa: E402
from modules.market_data import SP500_SAMPLE, load_prices  # noqa: E402

st.set_page_config(
    page_title="Algohns Quant Platform",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------- cached IO


@st.cache_data(ttl=3600, show_spinner="Carico i prezzi…")
def cached_prices(
    tickers: tuple[str, ...], start: date, end: date, synthetic: bool
):
    """Price loader. Tickers are a tuple so the cache key is hashable."""
    res = load_prices(list(tickers), start, end, synthetic=synthetic)
    return res.prices, res.source, res.warnings


@st.cache_resource
def get_sec_aggregator():
    """One aggregator per session; it holds the cached SEC ticker roster."""
    from modules.sec_aggregator import SecAggregator

    return SecAggregator()


@st.cache_resource
def get_connector():
    """Alpaca connector, or None when credentials are absent."""
    from modules.alpaca_execution import AlpacaConnector

    try:
        return AlpacaConnector()
    except ConfigError:
        return None


# ------------------------------------------------------------------ sidebar


def render_sidebar() -> dict:
    """Shared controls and an honest integration status panel."""
    st.sidebar.title("◆ Algohns")
    st.sidebar.caption("Quant Asset Manager OS — Paper only")

    st.sidebar.subheader("Universo")
    default = list(SP500_SAMPLE)[:12]
    tickers = st.sidebar.multiselect(
        "Ticker", options=list(SP500_SAMPLE), default=default,
        help="Campione S&P 500 diversificato per settore.",
    )
    years = st.sidebar.slider("Anni di storico", 1, 10, 5)
    synthetic = st.sidebar.toggle(
        "Dati sintetici", value=False,
        help=(
            "Serie riproducibili e correlate, per demo e test senza rete. "
            "Etichettate come tali: non usarle per decisioni."
        ),
    )

    st.sidebar.divider()
    st.sidebar.subheader("Stato integrazioni")
    _status_row("Alpaca (paper)", settings.has_alpaca,
                "credenziali assenti — vedi .env.example")
    try:
        settings.require_sec_user_agent()
        _status_row("SEC EDGAR", True, "")
    except ConfigError:
        _status_row("SEC EDGAR", False, "SEC_USER_AGENT senza contatto reale")

    for label, module in (
        ("alpaca-py", "alpaca"),
        ("yfinance", "yfinance"),
        ("QuantLib", "QuantLib"),
        ("spaCy", "spacy"),
        ("PyVis", "pyvis"),
        ("PyPortfolioOpt", "pypfopt"),
    ):
        _status_row(label, _installed(module), "non installato (opzionale)")

    from workers.scheduler import active_backend

    st.sidebar.caption(f"Scheduler attivo: **{active_backend()}**")

    st.sidebar.divider()
    st.sidebar.error("Esecuzione con denaro reale bloccata a livello di client.")

    end = date.today()
    return {
        "tickers": tuple(tickers),
        "start": end - timedelta(days=365 * years),
        "end": end,
        "synthetic": synthetic,
    }


def _installed(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _status_row(label: str, ok: bool, hint: str) -> None:
    icon = "🟢" if ok else "⚪"
    suffix = "" if ok else f" — {hint}"
    st.sidebar.caption(f"{icon} {label}{suffix}")


# --------------------------------------------------------------------- main


def main() -> None:
    ctx = render_sidebar()
    st.session_state["ctx"] = ctx

    st.title("Algohns Quant Platform")
    st.caption(
        "Bond europei · Esecuzione Alpaca · Backtesting · Supply chain · Bilanci SEC"
    )

    cols = st.columns(5)
    modules = [
        ("Bond Engine", "YTM netto, duration, convexity, fiscalità multi-paese"),
        ("Alpaca Engine", "Ordini e ribilanciamento asincrono, solo paper"),
        ("Backtest Suite", "Walk-forward causale, 6 obiettivi di ottimizzazione"),
        ("Supply Chain", "Grafo S&P 500 e contagio a due orologi"),
        ("SEC Statements", "XBRL normalizzato, confronto multi-ticker"),
    ]
    for col, (name, desc) in zip(cols, modules):
        with col:
            st.metric(name, "")
            st.caption(desc)

    st.divider()

    st.subheader("Da dove partire")
    st.markdown(
        """
Le pagine sono nel menu a sinistra. Ognuna funziona in isolamento, ma si
compongono:

1. **Bond Engine** non richiede credenziali: è matematica pura, si può usare subito.
2. **Backtest Suite** genera i pesi target che la pagina Alpaca poi esegue.
3. **Supply Chain** produce gli shock che stressano quei pesi.
4. **SEC Statements** fornisce la geografia dei ricavi che il grafo usa per
   mappare i paesi — non la sede legale.

**Dati sintetici** (toggle in sidebar) rendono tutto utilizzabile senza rete
né chiavi. Sono etichettati in ogni pagina che li usa.
        """
    )

    if not ctx["tickers"]:
        st.warning("Seleziona almeno un ticker nella sidebar per iniziare.")
        return

    with st.expander("Anteprima prezzi dell'universo selezionato", expanded=False):
        try:
            prices, source, warnings = cached_prices(
                ctx["tickers"], ctx["start"], ctx["end"], ctx["synthetic"]
            )
        except ValueError as exc:
            st.error(f"Caricamento prezzi fallito: {exc}")
            return

        badge = "🟡 sintetici" if source == "synthetic" else f"🟢 {source}"
        st.caption(f"Fonte: {badge} · {prices.shape[0]} barre · {prices.shape[1]} titoli")
        for w in warnings:
            st.info(w)
        st.line_chart(prices / prices.iloc[0] * 100.0, height=320)
        st.caption("Normalizzato a 100 alla prima osservazione.")


if __name__ == "__main__":
    main()
