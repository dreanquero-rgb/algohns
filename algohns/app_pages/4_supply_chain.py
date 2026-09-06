"""Streamlit page — Module 4: S&P 500 Supply Chain Graph Analytics."""
from __future__ import annotations

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from algohns.config import get_settings
from algohns.core.utils import is_available
from algohns.modules import supply_chain_graph as scg
from algohns.modules.supply_chain_graph import SupplyChainAnalyzer, sample_results
from algohns.modules.reference_data import sector_breakdown, sp500_constituents
from algohns import charts as ch
from algohns.ui import dependency_notice, header

header(
    "S&P 500 Supply Chain Graph Analytics",
    "Mappa fornitori–clienti dai 10-K/10-Q e analisi del rischio di contagio.",
    badge="Module 4",
)

settings = get_settings()
c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
tickers = c1.text_input("Focal tickers", value="AAPL NVDA TSLA MSFT AMZN")
form = c2.selectbox("Filing", ["10-K", "10-Q"])
limit = c3.number_input("Filings/ticker", 1, 3, 1)
use_sample = c4.toggle("Sample data", value=True,
                       help="Use the bundled example graph (SEC is blocked in this sandbox; "
                            "turn off on deploy to mine live filings).")

if not is_available(scg._spacy):  # noqa: SLF001
    st.info("spaCy not installed → RegEx-only extraction. For better NER: "
            "`pip install spacy && python -m spacy download en_core_web_sm`.")

if st.button("Build supply-chain graph", type="primary"):
    analyzer = SupplyChainAnalyzer()
    tick_list = [t.strip().upper() for t in tickers.replace(",", " ").split() if t.strip()]
    results = []
    if use_sample:
        results = [r for r in sample_results() if not tick_list or r.company in tick_list] or sample_results()
    else:
        prog = st.progress(0.0, text="Fetching filings…")
        try:
            for i, t in enumerate(tick_list):
                prog.progress((i + 1) / max(len(tick_list), 1), text=f"Analysing {t}…")
                results.append(analyzer.analyse(t, form=form, limit=int(limit)))
            prog.empty()
        except Exception as exc:  # noqa: BLE001
            dependency_notice(exc)
            st.stop()

    try:
        graph = analyzer.build_graph(results)
        metrics = analyzer.systemic_metrics(graph)
    except Exception as exc:  # noqa: BLE001
        dependency_notice(exc)
        st.stop()

    all_rels = [r for res in results for r in res.relationships]
    st.subheader(f"Extracted {len(all_rels)} relationships")

    if metrics:
        m = st.columns(3)
        m[0].metric("Nodes", metrics["nodes"])
        m[1].metric("Edges", metrics["edges"])
        m[2].metric("Density", metrics["density"])

    # Legend (fixes the invisible grey-on-black edges).
    st.markdown("**Legend**")
    legend = " &nbsp; ".join(
        f'<span style="color:{color};font-weight:700">&#9679; {label}</span>'
        for label, color in SupplyChainAnalyzer.EDGE_LEGEND.items()
    )
    st.markdown(legend, unsafe_allow_html=True)

    # Interactive graph.
    try:
        out = settings.cache_dir / "supply_chain.html"
        analyzer.render_pyvis(graph, out)
        components.html(out.read_text(), height=740, scrolling=True)
    except Exception as exc:  # noqa: BLE001
        st.caption(f"Interactive graph unavailable ({exc}). `pip install pyvis` to enable.")

    if metrics.get("top_systemic"):
        ts = pd.DataFrame(metrics["top_systemic"])
        cc = st.columns(2)
        with cc[0]:
            st.plotly_chart(
                ch.hbar(ts["node"], ts["pagerank"], title="Systemic importance (PageRank)",
                        height=340, value_fmt="{:.3f}"),
                use_container_width=True)
        with cc[1]:
            st.plotly_chart(
                ch.hbar(ts["node"], ts["contagion_reach"], slot=1,
                        title="Contagion reach (downstream nodes)", height=340,
                        value_fmt="{:.0f}"),
                use_container_width=True)
        with st.expander("Systemic metrics table"):
            st.dataframe(ts, use_container_width=True, hide_index=True)

    # Relationship mix
    if all_rels:
        mix = pd.Series([r.relation for r in all_rels]).value_counts()
        st.plotly_chart(
            ch.donut(mix.index, mix.values, title="Relationship mix", height=300),
            use_container_width=True)

    if all_rels:
        with st.expander("All relationships"):
            st.dataframe(
                pd.DataFrame([{"from": r.source, "to": r.target, "relation": r.relation,
                               "evidence": r.evidence} for r in all_rels]),
                use_container_width=True, hide_index=True,
            )


# =============================================================================
# Real S&P 500 index composition (bundled dataset — works offline)
# =============================================================================
with st.expander("🏛️ S&P 500 index composition (real data)", expanded=False):
    const = sp500_constituents()
    if const.empty:
        st.info("Constituents dataset not bundled.")
    else:
        st.caption(f"Real index membership — {len(const)} companies with GICS sector and SEC CIK.")
        sectors = sector_breakdown()
        st.plotly_chart(
            ch.hbar(sectors.index, sectors.values, title="Companies per GICS sector",
                    height=380, value_fmt="{:.0f}"),
            use_container_width=True)
        st.dataframe(const, use_container_width=True, hide_index=True, height=320)
