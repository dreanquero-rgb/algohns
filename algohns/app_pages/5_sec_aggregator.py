"""Streamlit page — Module 5: Consolidated SEC Financial Statements Aggregator."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from algohns.modules.sec_aggregator import STATEMENT_TAGS, SECAggregator
from algohns.ui import dependency_notice, header


def _fmt(df: pd.DataFrame) -> pd.DataFrame:
    """Human-readable formatting of large financial figures."""
    def human(x):
        if not isinstance(x, (int, float)) or pd.isna(x):
            return x
        for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
            if abs(x) >= div:
                return f"{x/div:,.2f}{unit}"
        return f"{x:,.2f}"
    # pandas 2.1+ uses DataFrame.map; older versions use applymap.
    elementwise = getattr(df, "map", None) or df.applymap
    return elementwise(human)


header(
    "Consolidated SEC Financial Statements",
    "XBRL Income Statement · Balance Sheet · Cash Flow — compared side-by-side.",
    badge="Module 5",
)

tickers = st.text_input("Tickers to compare", value="AAPL MSFT GOOGL")
tick_list = [t.strip().upper() for t in tickers.replace(",", " ").split() if t.strip()]

if st.button("Fetch & compare statements", type="primary"):
    agg = SECAggregator()
    try:
        with st.spinner("Querying SEC EDGAR company facts…"):
            frames = agg.compare_all(tick_list)
    except Exception as exc:  # noqa: BLE001
        dependency_notice(exc)
        st.stop()

    labels = {
        "income_statement": "📈 Income Statement",
        "balance_sheet": "🏦 Balance Sheet",
        "cash_flow": "💵 Cash Flow",
    }
    tabs = st.tabs(list(labels.values()) + ["📐 Key Ratios"])

    for tab, key in zip(tabs, STATEMENT_TAGS.keys()):
        with tab:
            df = frames[key]
            st.dataframe(_fmt(df), use_container_width=True)
            numeric = df.apply(pd.to_numeric, errors="coerce")
            if not numeric.dropna(how="all").empty:
                st.bar_chart(numeric.T)

    # Ratios tab
    with tabs[-1]:
        rows = {}
        for t in tick_list:
            try:
                rows[t] = agg.key_ratios(agg.company_facts(t))
            except Exception as exc:  # noqa: BLE001
                rows[t] = {"error": str(exc)}
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        st.caption("Net/Gross Margin, ROE, ROA, Current Ratio and Debt/Equity from latest annual XBRL facts.")

st.caption("Data © SEC EDGAR (data.sec.gov). No manual document download required.")
