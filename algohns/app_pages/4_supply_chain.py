"""Streamlit page — Module 4: S&P 500 Supply Chain Graph Analytics."""
from __future__ import annotations

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from algohns.config import get_settings
from algohns.core.utils import is_available
from algohns.modules import supply_chain_graph as scg
from algohns.modules.supply_chain_graph import SupplyChainAnalyzer
from algohns.ui import dependency_notice, header

header(
    "S&P 500 Supply Chain Graph Analytics",
    "Mine 10-K / 10-Q filings for supplier–customer links and map systemic risk.",
    badge="Module 4",
)

settings = get_settings()
st.caption(f"SEC User-Agent: `{settings.sec_user_agent}` — set SEC_USER_AGENT to your name + email.")

c1, c2, c3 = st.columns([2, 1, 1])
tickers = c1.text_input("Focal tickers", value="AAPL NVDA TSLA")
form = c2.selectbox("Filing", ["10-K", "10-Q"])
limit = c3.number_input("Filings per ticker", 1, 3, 1)

if not is_available(scg._spacy):  # noqa: SLF001
    st.info("spaCy not installed → RegEx-only extraction. For better NER: "
            "`pip install spacy && python -m spacy download en_core_web_sm`.")

if st.button("Build supply-chain graph", type="primary"):
    analyzer = SupplyChainAnalyzer()
    results = []
    prog = st.progress(0.0, text="Fetching filings…")
    tick_list = [t.strip().upper() for t in tickers.replace(",", " ").split() if t.strip()]
    try:
        for i, t in enumerate(tick_list):
            prog.progress((i + 1) / max(len(tick_list), 1), text=f"Analysing {t}…")
            results.append(analyzer.analyse(t, form=form, limit=int(limit)))
        prog.empty()
        graph = analyzer.build_graph(results)
        metrics = analyzer.systemic_metrics(graph)
    except Exception as exc:  # noqa: BLE001
        dependency_notice(exc)
        st.stop()

    all_rels = [r for res in results for r in res.relationships]
    st.subheader(f"Extracted {len(all_rels)} relationships")
    if all_rels:
        rel_df = pd.DataFrame([{"from": r.source, "to": r.target, "relation": r.relation,
                                "evidence": r.evidence} for r in all_rels])
        st.dataframe(rel_df, use_container_width=True, hide_index=True)
    else:
        st.info("No relationships extracted. Try a 10-K, or a company with explicit supplier/customer disclosure.")

    if metrics:
        m = st.columns(3)
        m[0].metric("Nodes", metrics["nodes"])
        m[1].metric("Edges", metrics["edges"])
        m[2].metric("Density", metrics["density"])
        st.markdown("**Most systemically important nodes (PageRank + contagion reach)**")
        st.dataframe(pd.DataFrame(metrics["top_systemic"]), use_container_width=True, hide_index=True)

        # Interactive PyVis render, if available.
        try:
            out = settings.cache_dir / "supply_chain.html"
            analyzer.render_pyvis(graph, out)
            components.html(out.read_text(), height=740, scrolling=True)
        except Exception as exc:  # noqa: BLE001
            st.caption(f"Interactive graph unavailable ({exc}). `pip install pyvis` to enable.")
