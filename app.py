"""Algohns V12 — Streamlit orchestrator.

High-performance multipage dashboard that stitches the five platform modules
into one Quant Asset Manager OS. Run with:

    streamlit run app.py

Uses the modern ``st.navigation`` API for a fast, single-process multipage app.
"""
from __future__ import annotations

import streamlit as st

from algohns import __version__
from algohns.config import get_settings
from algohns.ui import GOLD, inject_theme

st.set_page_config(
    page_title="Algohns V12 — Quant Asset Manager OS",
    page_icon="🅰️",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_theme()


# ---------------------------------------------------------------------------
# Home / Control Center
# ---------------------------------------------------------------------------
def home() -> None:
    settings = get_settings()
    st.markdown('<span class="algohns-badge">Algohns V12 · Python</span>', unsafe_allow_html=True)
    st.title("Quant Asset Manager OS")
    st.caption(
        "European fixed income · Alpaca paper auto-trading · portfolio optimization · "
        "SEC supply-chain graph · consolidated financial statements."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Version", __version__)
    c2.metric("Alpaca", "Connected" if settings.alpaca_configured else "Not set")
    c3.metric("Mode", "PAPER" if settings.alpaca_paper else "LOCKED")
    c4.metric("Tax residence", settings.default_tax_residence)

    st.divider()
    cols = st.columns(5)
    modules = [
        ("① Bond Engine", "Net YTM, duration, convexity + Italian/EU multi-tax."),
        ("② Auto-Trading", "Alpaca paper execution & async portfolio sync."),
        ("③ Backtest Suite", "Optimization (Max Sharpe, Min-Var, Risk Parity, BL)."),
        ("④ Supply Chain", "S&P 500 10-K/10-Q graph & contagion analytics."),
        ("⑤ SEC Aggregator", "XBRL income / balance / cash-flow comparison."),
    ]
    for col, (name, desc) in zip(cols, modules):
        with col:
            st.markdown(f'<div class="algohns-card"><b>{name}</b><br><small>{desc}</small></div>',
                        unsafe_allow_html=True)

    st.divider()
    with st.expander("⚙️ Configuration (secrets masked)"):
        st.json(settings.masked())
        st.caption(
            "Set ALPACA_API_KEY / ALPACA_SECRET_KEY / REDIS_URL / SEC_USER_AGENT via "
            "environment variables or a local .env file. Copy .env.example to get started."
        )

    st.divider()
    st.markdown(
        f"<small style='color:{GOLD}'>Real-money execution is locked platform-wide. "
        "All trading is Alpaca paper only.</small>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
pages = {
    "Overview": [
        st.Page(home, title="Control Center", icon="🏦", default=True),
    ],
    "Platform Modules": [
        st.Page("algohns/app_pages/1_bond_engine.py", title="Bond Yield & Tax", icon="📈"),
        st.Page("algohns/app_pages/2_auto_trading.py", title="Alpaca Auto-Trading", icon="🤖"),
        st.Page("algohns/app_pages/3_backtest_suite.py", title="Backtest & Optimize", icon="🧪"),
        st.Page("algohns/app_pages/4_supply_chain.py", title="Supply Chain Graph", icon="🕸️"),
        st.Page("algohns/app_pages/5_sec_aggregator.py", title="SEC Statements", icon="📊"),
    ],
}

nav = st.navigation(pages, position="sidebar")
nav.run()
