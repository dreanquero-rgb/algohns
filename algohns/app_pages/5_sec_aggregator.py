"""Streamlit page — Module 5: Consolidated SEC Financial Statements."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from algohns import charts as ch
from algohns.modules.sec_aggregator import STATEMENT_TAGS, SECAggregator, sample_facts
from algohns.ui import dependency_notice, header

LABELS = {"income_statement": "📈 Income Statement",
          "balance_sheet": "🏦 Balance Sheet", "cash_flow": "💵 Cash Flow"}


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


def _row(df: pd.DataFrame, label: str) -> pd.Series | None:
    if df.empty or label not in df.index:
        return None
    s = pd.to_numeric(df.loc[label], errors="coerce").dropna()
    return s if not s.empty else None


header(
    "Consolidated SEC Financial Statements",
    "Bilanci interi (Income Statement · Balance Sheet · Cash Flow) con KPI e grafici.",
    badge="Module 5",
)

mode = st.radio("Mode", ["📄 Single company (full statements)", "📊 Compare tickers"],
                horizontal=True)
agg = SECAggregator()

# =============================================================================
# SINGLE COMPANY
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
            dependency_notice(exc); st.stop()

        st.subheader(facts.entity_name)

        # ---- KPI tiles -------------------------------------------------------
        kpis = agg.kpis(facts)
        st.markdown("### Key figures")
        keys = list(kpis.keys())
        for start in range(0, len(keys), 4):
            cols = st.columns(4)
            for col, label in zip(cols, keys[start:start + 4]):
                k = kpis[label]; val = k["value"]
                display = (f"{val:,.2f}" if "EPS" in label else _human(val)) if val is not None else "—"
                delta = f"{k['yoy']*100:+.1f}% YoY" if k["yoy"] is not None else None
                col.metric(f"{label} (FY{k['year']})" if k["year"] else label, display, delta=delta)

        stmts = {k: agg.full_statement(facts, k, years=years) for k in STATEMENT_TAGS}
        st.markdown("### Full statements")
        tabs = st.tabs(list(LABELS.values()))

        # ---- Income statement ------------------------------------------------
        with tabs[0]:
            inc = stmts["income_statement"]
            rev, gp, oi, ni = (_row(inc, x) for x in
                               ("Revenue", "Gross Profit", "Operating Income", "Net Income"))
            cogs = _row(inc, "Cost of Revenue")
            if rev is not None and ni is not None:
                fy = rev.index[-1]
                # Waterfall: how revenue becomes net income (latest FY)
                labels, values, measures = ["Revenue"], [float(rev.iloc[-1])], ["absolute"]
                if cogs is not None and fy in cogs.index:
                    labels.append("Cost of revenue"); values.append(-float(cogs[fy])); measures.append("relative")
                if gp is not None and oi is not None and fy in gp.index and fy in oi.index:
                    labels.append("Operating expenses"); values.append(-(float(gp[fy]) - float(oi[fy]))); measures.append("relative")
                if oi is not None and fy in oi.index:
                    labels.append("Below the line"); values.append(float(ni[fy]) - float(oi[fy])); measures.append("relative")
                labels.append("Net income"); values.append(None); measures.append("total")
                st.plotly_chart(ch.waterfall(labels, values, measures,
                                             title=f"How revenue becomes net income (FY{fy})"),
                                use_container_width=True)
                cc = st.columns(2)
                with cc[0]:
                    trend = pd.DataFrame({"Revenue": rev, "Net income": ni}).dropna()
                    st.plotly_chart(ch.grouped_bar(trend, title="Revenue vs net income by year"),
                                    use_container_width=True)
                with cc[1]:
                    marg = pd.DataFrame(index=rev.index)
                    if gp is not None: marg["Gross margin"] = (gp / rev * 100).round(2)
                    if oi is not None: marg["Operating margin"] = (oi / rev * 100).round(2)
                    marg["Net margin"] = (ni / rev * 100).round(2)
                    st.plotly_chart(ch.line(marg.dropna(how="all"), title="Margins (%)"),
                                    use_container_width=True)
            st.dataframe(_fmt_df(inc), use_container_width=True)

        # ---- Balance sheet ---------------------------------------------------
        with tabs[1]:
            bs = stmts["balance_sheet"]
            assets, liab, eq = (_row(bs, x) for x in ("Total Assets", "Total Liabilities", "Total Equity"))
            if assets is not None and liab is not None and eq is not None:
                cc = st.columns(2)
                with cc[0]:
                    comp = pd.DataFrame({"Liabilities": liab, "Equity": eq}).dropna()
                    st.plotly_chart(ch.stacked_bar(comp, title="Capital structure by year"),
                                    use_container_width=True)
                with cc[1]:
                    st.plotly_chart(ch.line(pd.DataFrame({"Total assets": assets}),
                                            title="Total assets"), use_container_width=True)
            st.dataframe(_fmt_df(bs), use_container_width=True)

        # ---- Cash flow -------------------------------------------------------
        with tabs[2]:
            cf = stmts["cash_flow"]
            ocf, icf, fcf_ = (_row(cf, x) for x in
                              ("Operating Cash Flow", "Investing Cash Flow", "Financing Cash Flow"))
            capex = _row(cf, "CapEx")
            if ocf is not None:
                flows = pd.DataFrame({"Operating": ocf})
                if icf is not None: flows["Investing"] = icf
                if fcf_ is not None: flows["Financing"] = fcf_
                st.plotly_chart(ch.grouped_bar(flows.dropna(how="all"),
                                               title="Cash flows by activity"),
                                use_container_width=True)
                if capex is not None:
                    free = (ocf - capex).dropna()
                    st.plotly_chart(ch.bar([str(i) for i in free.index], free.values,
                                           title="Free cash flow (OCF − CapEx)",
                                           color_by_sign=True, height=300),
                                    use_container_width=True)
            st.dataframe(_fmt_df(cf), use_container_width=True)

# =============================================================================
# COMPARE TICKERS
# =============================================================================
else:
    tickers = st.text_input("Tickers to compare", value="AAPL MSFT GOOGL")
    use_sample = st.toggle("Sample data", value=True)
    tick_list = [t.strip().upper() for t in tickers.replace(",", " ").split() if t.strip()]

    if st.button("Fetch & compare", type="primary"):
        try:
            if use_sample:
                facts_map = {t: sample_facts(t) for t in tick_list}
                frames = {stmt: pd.DataFrame({t: agg.statement(f, stmt) for t, f in facts_map.items()})
                          for stmt in STATEMENT_TAGS}
            else:
                frames = agg.compare_all(tick_list)
                facts_map = {t: agg.company_facts(t) for t in tick_list}
        except Exception as exc:  # noqa: BLE001
            dependency_notice(exc); st.stop()

        tabs = st.tabs(list(LABELS.values()) + ["📐 Key Ratios"])
        for tab, key in zip(tabs, STATEMENT_TAGS):
            with tab:
                frame = frames[key]
                numeric = frame.apply(pd.to_numeric, errors="coerce")
                head = numeric.dropna(how="all").head(4)
                if not head.empty:
                    # rows = line items on x, one series per ticker
                    st.plotly_chart(ch.grouped_bar(head, title=f"{key.replace('_',' ').title()} — key lines"),
                                    use_container_width=True)
                st.dataframe(_fmt_df(frame), use_container_width=True)
        with tabs[-1]:
            rows = {t: agg.key_ratios(f) for t, f in facts_map.items()}
            ratios = pd.DataFrame(rows)
            st.plotly_chart(ch.grouped_bar(ratios.apply(pd.to_numeric, errors="coerce").dropna(how="all"),
                                           title="Key ratios"), use_container_width=True)
            st.dataframe(ratios, use_container_width=True)

st.caption("Data © SEC EDGAR (data.sec.gov) XBRL company facts — no manual document download.")
