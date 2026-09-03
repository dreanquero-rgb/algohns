"""Shared Streamlit UI helpers and Algohns branding.

Keeps the gold / cyan / midnight palette inherited from Algohns V11 so the
Python dashboard feels continuous with the previous Cloudflare Worker UI.
"""
from __future__ import annotations

import streamlit as st

GOLD = "#E2B86B"
CYAN = "#38BDF8"
MIDNIGHT = "#070B13"
SLATE = "#0F172A"
INK = "#F8FAFC"

_CSS = f"""
<style>
:root {{
    --gold: {GOLD};
    --cyan: {CYAN};
    --midnight: {MIDNIGHT};
}}
.stApp {{
    background: radial-gradient(1200px 600px at 20% -10%, #10233a 0%, {MIDNIGHT} 55%);
}}
h1, h2, h3 {{ letter-spacing: .3px; }}
.algohns-badge {{
    display:inline-block; padding:2px 10px; border-radius:999px;
    background:linear-gradient(90deg, {GOLD}, {CYAN}); color:{MIDNIGHT};
    font-weight:700; font-size:.72rem; text-transform:uppercase;
}}
.algohns-card {{
    background: rgba(15,23,42,.65); border:1px solid rgba(56,189,248,.18);
    border-radius:16px; padding:18px 20px; margin-bottom:12px;
}}
div[data-testid="stMetricValue"] {{ color: {GOLD}; }}
.paper-lock {{
    background: rgba(226,184,107,.12); border:1px solid {GOLD};
    color:{GOLD}; padding:8px 14px; border-radius:12px; font-weight:600;
}}
</style>
"""


def inject_theme() -> None:
    """Apply the Algohns theme (call once per page)."""
    st.markdown(_CSS, unsafe_allow_html=True)


def header(title: str, subtitle: str = "", badge: str = "") -> None:
    inject_theme()
    if badge:
        st.markdown(f'<span class="algohns-badge">{badge}</span>', unsafe_allow_html=True)
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def paper_lock_banner() -> None:
    st.markdown(
        '<div class="paper-lock">🔒 PAPER TRADING ONLY — real-money execution is locked platform-wide.</div>',
        unsafe_allow_html=True,
    )


def dependency_notice(exc: Exception) -> None:
    """Render an actionable message when an optional dependency is missing."""
    st.warning(f"⚙️ {exc}")
    st.caption("Install the extra listed above, then rerun this page.")
