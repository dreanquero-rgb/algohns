"""Algohns V12 — Streamlit orchestrator.

High-performance multipage dashboard that stitches the five platform modules
into one platform. Run with:

    streamlit run app.py

Uses the modern ``st.navigation`` API for a fast, single-process multipage app.
"""
from __future__ import annotations

import os

import streamlit as st

from algohns import __version__
from algohns.config import get_settings
from algohns.ui import GOLD, inject_theme

st.set_page_config(
    page_title="Algohns",
    page_icon="🅰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Secrets bridge ----------------------------------------------------------
# The platform reads configuration from environment variables (locally via a
# .env file). On Streamlit Community Cloud there is no .env — secrets live in
# st.secrets — so copy any that are set there into the environment before the
# shared settings object is built. Nothing here is overwritten if it is already
# set, so local .env behaviour is unchanged, and missing secrets are simply
# skipped (the app still runs, features that need a key just show "not set").
_SECRET_KEYS = (
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_PAPER", "ALPACA_BASE_URL",
    "ALPACA_DATA_BASE_URL", "REDIS_URL", "SEC_USER_AGENT", "DEFAULT_TAX_RESIDENCE",
    "ALGO_AUTO_REBALANCE", "ALGO_STRATEGY", "ALGO_REBALANCE_CRON",
    "ALGO_TARGET_WEIGHTS", "ALGO_SYNC_INTERVAL", "ALGO_ALLOW_CODE_EXEC",
    "ALGO_APP_PASSCODE",
)
try:
    for _k in _SECRET_KEYS:
        if _k not in os.environ:
            _v = st.secrets.get(_k)  # raises if no secrets file exists at all
            if _v is not None:
                os.environ[_k] = str(_v)
    get_settings.cache_clear()  # rebuild settings now that the env is populated
except Exception:  # noqa: BLE001 - no secrets locally is the normal case
    pass

inject_theme()


# ---------------------------------------------------------------------------
# Private access gate
# ---------------------------------------------------------------------------
def _require_login() -> None:
    """Passcode gate that keeps the whole dashboard — and especially the code
    editor and code execution — private.

    A public URL that could run edited Python would be an RCE and a key-exposure
    hole, so nothing renders until the creator code is entered. The code is read
    from ``ALGO_APP_PASSCODE`` (host secret / env var) and falls back to a
    built-in default only so the app works out of the box. **Set the secret on
    your host for real privacy** — the repo default is visible to anyone.
    """
    if st.session_state.get("_authed"):
        return

    expected = (os.environ.get("ALGO_APP_PASSCODE") or "").strip()
    try:
        _s = st.secrets.get("ALGO_APP_PASSCODE")  # raises if no secrets file
        if _s:
            expected = str(_s).strip()
    except Exception:  # noqa: BLE001 - no secrets locally is normal
        pass
    expected = expected or "0407"  # convenience default; override via secret

    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown('<span class="algohns-badge">Algohns · area privata</span>',
                    unsafe_allow_html=True)
        st.title("🔒 Accesso")
        st.caption("Inserisci il codice creatore per entrare.")
        code = st.text_input(
            "Codice", type="password", key="_login_code",
            label_visibility="collapsed", placeholder="Codice creatore",
        )
        if st.button("Entra", type="primary", use_container_width=True):
            if code and code.strip() == expected:
                st.session_state["_authed"] = True
                # Being behind this gate is what makes running edited code safe,
                # so unlock the sandbox for this now-private instance.
                os.environ["ALGO_ALLOW_CODE_EXEC"] = "true"
                get_settings.cache_clear()
                st.rerun()
            else:
                st.error("Codice errato.")
    st.stop()


_require_login()

with st.sidebar:
    if st.button("🔓 Esci", key="_logout", use_container_width=True):
        for _k in ("_authed", "_login_code"):
            st.session_state.pop(_k, None)
        st.rerun()


# ---------------------------------------------------------------------------
# Home / Control Center
# ---------------------------------------------------------------------------
def home() -> None:
    settings = get_settings()
    st.markdown('<span class="algohns-badge">Algohns V12 · Python</span>', unsafe_allow_html=True)
    st.title("Algohns")
    st.caption(
        "European fixed income & global government curves · Alpaca paper "
        "auto-trading · portfolio optimization · forward world simulation."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Version", __version__)
    c2.metric("Alpaca", "Connected" if settings.alpaca_configured else "Not set")
    c3.metric("Mode", "PAPER" if settings.alpaca_paper else "LOCKED")
    c4.metric("Tax residence", settings.default_tax_residence)

    st.divider()
    cols = st.columns(4)
    modules = [
        ("① Bond Yield & Tax", "Net YTM, duration, multi-tax + global government "
                               "curves (Nelson-Siegel fits & spreads)."),
        ("② Auto-Trading", "Alpaca paper execution & async portfolio sync."),
        ("③ Backtest Suite", "Optimization (Max Sharpe, Min-Var, Risk Parity, BL)."),
        ("④ World Simulation", "Forward test: stochastic price paths modulated by news."),
    ]
    for col, (name, desc) in zip(cols, modules):
        with col:
            st.markdown(f'<div class="algohns-card"><b>{name}</b><br><small>{desc}</small></div>',
                        unsafe_allow_html=True)

    st.divider()
    st.markdown("#### Bundled real datasets")
    try:
        from algohns.modules.reference_data import available
        cols = st.columns(3)
        for col, (name, rows) in zip(cols, available().items()):
            col.metric(name, f"{rows:,}" if rows else "—", help="Real data shipped with the repo")
    except Exception:  # noqa: BLE001
        st.caption("Reference datasets unavailable.")

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
        st.Page("algohns/app_pages/6_world_simulation.py", title="World Simulation", icon="🌍"),
    ],
}

nav = st.navigation(pages, position="sidebar")
nav.run()
