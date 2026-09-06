"""Streamlit page — Module 3: Universe Explorer + Backtesting & Optimization."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from algohns import charts as ch
from algohns.core.data_providers import get_market_data
from algohns.modules import universe
from algohns.modules.backtest_suite import Backtester, PortfolioOptimizer, compute_metrics
from algohns.modules.reference_data import spx_history
from algohns.ui import dependency_notice, header

header(
    "Universe Explorer + Backtesting & Optimization",
    "300k+ strumenti (FinanceDatabase) · Max Sharpe/Min-Var/Risk-Parity/Black-Litterman · storico dal 1871.",
    badge="Module 3",
)

tab_universe, tab_backtest, tab_history = st.tabs(
    ["🌐 Universe Explorer", "🧪 Optimize & Backtest", "🏛️ Long history (real)"]
)

# =============================================================================
# TAB 1 — UNIVERSE EXPLORER
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
            dependency_notice(exc); results = pd.DataFrame()

        st.caption(f"{len(results)} instruments (showing up to 500).")

        # Composition chart of the current slice
        for dim in ("sector", "category_group", "currency"):
            if dim in results.columns and results[dim].notna().any():
                counts = results[dim].value_counts().head(10)
                st.plotly_chart(
                    ch.hbar(counts.index, counts.values, title=f"Slice composition by {dim}",
                            height=300, value_fmt="{:.0f}"),
                    use_container_width=True)
                break

        st.dataframe(results, use_container_width=True, hide_index=True, height=360)
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
        start = c4.text_input("Start date (YYYY-MM-DD)", value="1995-01-01"); period = "max"
    else:
        period = c4.selectbox("History", ["1y", "2y", "5y", "10y", "max"], index=2); start = None
    benchmark = c5.text_input("Benchmark", value="SPY")
    rebalance = c6.selectbox("Rebalance", ["Q", "M", "Y", "none"], index=0)

    if st.button("Run optimization & backtest", type="primary"):
        md = get_market_data()
        src = "stooq" if source.startswith("stooq") else "yfinance"
        try:
            with st.spinner("Downloading prices…"):
                prices = md.history(tickers, period=period, source=src, start=start)
            if prices.empty or prices.shape[1] < 2:
                st.error("Not enough price data (check tickers; the network may be blocked "
                         "here — this works on deploy).")
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
            dependency_notice(exc); st.stop()

        # ---- headline metrics -------------------------------------------------
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

        # ---- equity curve vs benchmark ---------------------------------------
        curve = result.equity_curve.rename("Portfolio").to_frame()
        if result.benchmark_curve is not None:
            curve[benchmark.upper()] = result.benchmark_curve
        st.plotly_chart(ch.line(curve, title="Equity curve", height=380),
                        use_container_width=True)
        st.plotly_chart(ch.area(result.drawdown_curve.rename("Drawdown"),
                                title="Drawdown", negative=True),
                        use_container_width=True)

        cc = st.columns(2)
        # ---- weights ---------------------------------------------------------
        wser = pd.Series(weights).sort_values(ascending=False)
        with cc[0]:
            st.plotly_chart(
                ch.hbar(wser.index, wser.values * 100, title="Optimized weights",
                        height=340, value_fmt="{:.1f}", suffix="%"),
                use_container_width=True)
            e = st.columns(3)
            e[0].metric("Exp. return", f"{expected['expected_return']*100:.2f}%")
            e[1].metric("Exp. vol", f"{expected['expected_volatility']*100:.2f}%")
            e[2].metric("Exp. Sharpe", f"{expected['expected_sharpe']:.2f}")
        # ---- correlation matrix ---------------------------------------------
        with cc[1]:
            corr = prices.pct_change().dropna().corr()
            st.plotly_chart(ch.heatmap(corr, title="Correlation matrix", height=340),
                            use_container_width=True)

        # ---- rolling 1y Sharpe ----------------------------------------------
        rets = result.equity_curve.pct_change().dropna()
        if len(rets) > 260:
            roll = (rets.rolling(252).mean() / rets.rolling(252).std()) * np.sqrt(252)
            st.plotly_chart(ch.line(roll.dropna().rename("Rolling 1y Sharpe").to_frame(),
                                    title="Rolling 1-year Sharpe ratio", height=300),
                            use_container_width=True)

        st.caption(f"Data source: {src} · {len(result.equity_curve)} trading days "
                   f"({result.equity_curve.index.min().date()} → {result.equity_curve.index.max().date()})")
        with st.expander("Full metrics + VaR / CVaR"):
            st.json(result.summary())

# =============================================================================
# TAB 3 — REAL LONG HISTORY (bundled dataset, works offline)
# =============================================================================
with tab_history:
    hist = spx_history()
    if hist.empty:
        st.info("Long-history dataset not bundled.")
    else:
        st.caption(f"Real S&P 500 dataset bundled with the repo — {len(hist)} monthly "
                   f"observations, {hist.index.min().date()} → {hist.index.max().date()}.")
        start_year = st.slider("From year", int(hist.index.year.min()),
                               int(hist.index.year.max()) - 1, 1990)
        h = hist[hist.index.year >= start_year]

        k = st.columns(4)
        total = h["SP500"].iloc[-1] / h["SP500"].iloc[0] - 1
        yrs = max((h.index[-1] - h.index[0]).days / 365.25, 1e-9)
        k[0].metric("Total return", f"{total*100:,.0f}%")
        k[1].metric("CAGR", f"{((1+total)**(1/yrs)-1)*100:.2f}%")
        k[2].metric("Years", f"{yrs:.0f}")
        if "PE10" in h and h["PE10"].gt(0).any():
            k[3].metric("CAPE (PE10) today", f"{h['PE10'][h['PE10']>0].iloc[-1]:.1f}")

        st.plotly_chart(ch.line(h[["SP500"]].rename(columns={"SP500": "S&P 500"}),
                                title=f"S&P 500 index since {start_year} (log scale)",
                                height=380, log_y=True),
                        use_container_width=True)

        dd = h["SP500"] / h["SP500"].cummax() - 1
        st.plotly_chart(ch.area(dd.rename("Drawdown"), title="Historical drawdown", negative=True),
                        use_container_width=True)

        cols = st.columns(2)
        if "PE10" in h and h["PE10"].gt(0).any():
            with cols[0]:
                pe = h.loc[h["PE10"] > 0, ["PE10"]].rename(columns={"PE10": "CAPE (PE10)"})
                st.plotly_chart(ch.line(pe, title="Shiller CAPE ratio", height=300),
                                use_container_width=True)
        if "Long Interest Rate" in h and h["Long Interest Rate"].gt(0).any():
            with cols[1]:
                ir = h.loc[h["Long Interest Rate"] > 0, ["Long Interest Rate"]].rename(
                    columns={"Long Interest Rate": "Long interest rate %"})
                st.plotly_chart(ch.line(ir, title="Long-term interest rate", height=300),
                                use_container_width=True)

        # Decade returns — magnitude with polarity
        dec = h["SP500"].resample("10YS").first().pct_change().dropna() * 100
        if not dec.empty:
            st.plotly_chart(
                ch.bar([f"{d.year}s" for d in dec.index], dec.values,
                       title="Return by decade", suffix="%", color_by_sign=True, height=300),
                use_container_width=True)
