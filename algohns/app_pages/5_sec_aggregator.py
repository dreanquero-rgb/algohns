"""Streamlit page — Module 5: Consolidated SEC Financial Statements."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from algohns.modules.sec_aggregator import STATEMENT_TAGS, SECAggregator, sample_facts
from algohns.ui import dependency_notice, header


def _human(x):
    if not isinstance(x, (int, float)) or pd.isna(x):
        return x
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= div:
            return f"{x/div:,.2f}{unit}"
    return f"{x:,.2f}"


def _fmt_df(df: pd.DataFrame) -> pd.DataFrame:
    elementwise = getattr(df, "map", None) or df.applymap
    return elementwise(_human)


header(
    "Consolidated SEC Financial Statements",
    "Bilanci interi (Income Statement · Balance Sheet · Cash Flow) con KPI in evidenza.",
    badge="Module 5",
)

mode = st.radio("Mode", ["📄 Single company (full statements)", "📊 Compare tickers"], horizontal=True)
agg = SECAggregator()

# =============================================================================
# SINGLE COMPANY — full multi-year statements + KPI tiles
# =============================================================================
if mode.startswith("📄"):
    c1, c2, c3 = st.columns([2, 1, 1])
    ticker = c1.text_input("Ticker", value="AAPL")
    years = c2.slider("Years", 2, 8, 5)
    use_sample = c3.toggle("Sample data", value=True,
                           help="SEC EDGAR is blocked in this sandbox; on deploy turn off for live data.")

    if st.button("Load financial statements", type="primary"):
        try:
            facts = sample_facts(ticker) if use_sample else agg.company_facts(ticker)
        except Exception as exc:  # noqa: BLE001
            dependency_notice(exc)
            st.stop()

        st.subheader(facts.entity_name)

        # ---- KPI tiles (highlighted headline numbers) ----------------------
        kpis = agg.kpis(facts)
        st.markdown("### Key figures")
        keys = list(kpis.keys())
        for row_start in range(0, len(keys), 4):
            cols = st.columns(4)
            for col, label in zip(cols, keys[row_start:row_start + 4]):
                k = kpis[label]
                val = k["value"]
                is_eps = "EPS" in label
                display = (f"{val:,.2f}" if is_eps else _human(val)) if val is not None else "—"
                delta = f"{k['yoy']*100:+.1f}% YoY" if k["yoy"] is not None else None
                col.metric(f"{label} (FY{k['year']})" if k["year"] else label, display, delta=delta)

        # ---- Full statements (multi-year) ----------------------------------
        st.markdown("### Full statements")
        labels = {"income_statement": "📈 Income Statement",
                  "balance_sheet": "🏦 Balance Sheet", "cash_flow": "💵 Cash Flow"}
        tabs = st.tabs(list(labels.values()))
        for tab, key in zip(tabs, STATEMENT_TAGS):
            with tab:
                df = agg.full_statement(facts, key, years=years)
                if df.empty:
                    st.info("No data for this statement.")
                    continue
                st.dataframe(_fmt_df(df), use_container_width=True)
                # trend chart of the top line item across years
                numeric = df.apply(pd.to_numeric, errors="coerce")
                if not numeric.empty:
                    st.line_chart(numeric.T)

# =============================================================================
# COMPARE TICKERS — side by side
# =============================================================================
else:
    tickers = st.text_input("Tickers to compare", value="AAPL MSFT GOOGL")
    use_sample = st.toggle("Sample data", value=True)
    tick_list = [t.strip().upper() for t in tickers.replace(",", " ").split() if t.strip()]

    if st.button("Fetch & compare", type="primary"):
        try:
            if use_sample:
                facts_map = {t: sample_facts(t) for t in tick_list}
                frames = {}
                for stmt in STATEMENT_TAGS:
                    frames[stmt] = pd.DataFrame({t: agg.statement(f, stmt) for t, f in facts_map.items()})
            else:
                frames = agg.compare_all(tick_list)
                facts_map = {t: agg.company_facts(t) for t in tick_list}
        except Exception as exc:  # noqa: BLE001
            dependency_notice(exc)
            st.stop()

        labels = {"income_statement": "📈 Income Statement",
                  "balance_sheet": "🏦 Balance Sheet", "cash_flow": "💵 Cash Flow"}
        tabs = st.tabs(list(labels.values()) + ["📐 Key Ratios"])
        for tab, key in zip(tabs, STATEMENT_TAGS):
            with tab:
                st.dataframe(_fmt_df(frames[key]), use_container_width=True)
                numeric = frames[key].apply(pd.to_numeric, errors="coerce")
                if not numeric.dropna(how="all").empty:
                    st.bar_chart(numeric.T)
        with tabs[-1]:
            rows = {t: agg.key_ratios(f) for t, f in facts_map.items()}
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

st.caption("Data © SEC EDGAR (data.sec.gov) XBRL company facts — no manual document download.")
