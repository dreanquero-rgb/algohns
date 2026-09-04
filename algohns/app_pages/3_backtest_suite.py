"""Streamlit page — Module 3: Universe Explorer + Backtesting & Optimization."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from algohns.core.data_providers import get_market_data
from algohns.modules import universe
from algohns.modules.backtest_suite import Backtester, PortfolioOptimizer
from algohns.ui import dependency_notice, header

header(
    "Universe Explorer + Backtesting & Optimization",
    "300k+ instruments (FinanceDatabase) · Max Sharpe/Min-Var/Risk-Parity/Black-Litterman · history to the 1990s.",
    badge="Module 3",
)

tab_universe, tab_backtest = st.tabs(["🌐 Universe Explorer", "🧪 Optimize & Backtest"])

# =============================================================================
# TAB 1 — UNIVERSE EXPLORER (FinanceDatabase)
# =============================================================================
with tab_universe:
    if not universe.available():
        st.warning("FinanceDatabase not installed → `pip install financedatabase`.")
    else:
        c = st.columns([1, 2])
        asset_class = c[0].selectbox("Asset class", universe.ASSET_CLASSES)
        query = c[1].text_input("Search (symbol or name)", "")

        opts = universe.options(asset_class)
        filters: dict[str, str] = {}
        if opts:
            fields = [f for f in ("country", "sector", "industry", "category_group",
                                  "category", "currency", "exchange") if f in opts]
            fcols = st.columns(min(len(fields), 4) or 1)
            for i, field in enumerate(fields[:4]):
                values = list(opts[field])
                if values:
                    sel = fcols[i].selectbox(field.replace("_", " ").title(),
                                             ["(any)"] + values, key=f"flt_{field}")
                    if sel != "(any)":
                        filters[field] = sel

        try:
            results = universe.search(asset_class, filters=filters, query=query, limit=500)
        except Exception as exc:  # noqa: BLE001
            dependency_notice(exc)
            results = pd.DataFrame()

        st.caption(f"{len(results)} instruments (showing up to 500).")
        st.dataframe(results, use_container_width=True, hide_index=True, height=380)

        syms = universe.tickers_from(results)
        chosen = st.multiselect("Select tickers to backtest", syms, default=syms[:8])
        if st.button("➡️ Send selection to backtest", type="primary", disabled=not chosen):
            st.session_state["bt_tickers"] = " ".join(chosen)
            st.success(f"{len(chosen)} tickers sent to the Optimize & Backtest tab.")

# =============================================================================
# TAB 2 — OPTIMIZE & BACKTEST
# =============================================================================
with tab_backtest:
    default_tickers = st.session_state.get("bt_tickers", "AAPL MSFT NVDA AMZN GOOGL JPM XOM")
    c1, c2, c3 = st.columns([2, 1, 1])
    tickers = c1.text_input("Universe (space/comma separated)", value=default_tickers)
    source = c2.selectbox("Data source", ["yfinance", "stooq (to 1990s)"])
    method = c3.selectbox("Optimizer",
                          ["max_sharpe", "min_volatility", "risk_parity", "equal_weight", "black_litterman"])
    c4, c5, c6 = st.columns(3)
    if source.startswith("stooq"):
        start = c4.text_input("Start date (YYYY-MM-DD)", value="1995-01-01")
        period = "max"
    else:
        period = c4.selectbox("History", ["1y", "2y", "5y", "10y", "max"], index=2)
        start = None
    benchmark = c5.text_input("Benchmark", value="SPY")
    rebalance = c6.selectbox("Rebalance", ["Q", "M", "Y", "none"], index=0)

    if st.button("Run optimization & backtest", type="primary"):
        md = get_market_data()
        src = "stooq" if source.startswith("stooq") else "yfinance"
        try:
            with st.spinner("Downloading prices…"):
                prices = md.history(tickers, period=period, source=src, start=start)
            if prices.empty or prices.shape[1] < 2:
                st.error("Not enough price data (check tickers; network may be blocked here — works on deploy).")
                st.stop()

            optimizer = PortfolioOptimizer(prices)
            weights = optimizer.optimize(method)
            expected = optimizer.expected_performance(weights)
            bench_px = None
            try:
                bench_px = md.history(benchmark, period=period, source=src, start=start).iloc[:, 0]
            except Exception:  # noqa: BLE001
                pass
            result = Backtester(prices).run(weights, rebalance=rebalance, benchmark=bench_px)
        except Exception as exc:  # noqa: BLE001
            dependency_notice(exc)
            st.stop()

        st.subheader("Optimized weights")
        wdf = pd.DataFrame(list(weights.items()), columns=["Asset", "Weight"]).sort_values("Weight", ascending=False)
        cols = st.columns([1, 1])
        cols[0].dataframe(wdf, use_container_width=True, hide_index=True)
        cols[1].bar_chart(wdf.set_index("Asset"))
        e = st.columns(3)
        e[0].metric("Expected return", f"{expected['expected_return']*100:.2f}%")
        e[1].metric("Expected vol", f"{expected['expected_volatility']*100:.2f}%")
        e[2].metric("Expected Sharpe", f"{expected['expected_sharpe']:.2f}")

        st.subheader("Backtest performance")
        metrics = result.metrics.as_dict()
        m = st.columns(4)
        m[0].metric("CAGR", f"{metrics['cagr']*100:.2f}%")
        m[1].metric("Sharpe", f"{metrics['sharpe']:.2f}")
        m[2].metric("Sortino", f"{metrics['sortino']:.2f}")
        m[3].metric("Calmar", f"{metrics['calmar']:.2f}")
        m2 = st.columns(4)
        m2[0].metric("Max Drawdown", f"{metrics['max_drawdown']*100:.2f}%")
        m2[1].metric("Volatility", f"{metrics['annual_volatility']*100:.2f}%")
        m2[2].metric("Alpha", f"{metrics['alpha']*100:.2f}%")
        m2[3].metric("Beta", f"{metrics['beta']:.2f}")

        curve = result.equity_curve.rename("Portfolio").to_frame()
        if result.benchmark_curve is not None:
            curve[benchmark.upper()] = result.benchmark_curve
        st.markdown("**Equity curve**")
        st.line_chart(curve)
        st.markdown("**Drawdown**")
        st.area_chart(result.drawdown_curve.rename("Drawdown"))
        st.caption(f"Data source: {src} · {len(result.equity_curve)} trading days "
                   f"({result.equity_curve.index.min().date()} → {result.equity_curve.index.max().date()})")
        with st.expander("Full metrics + VaR / CVaR"):
            st.json(result.summary())
