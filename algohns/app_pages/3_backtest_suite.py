"""Streamlit page — Module 3: Backtesting & Portfolio Optimization Suite."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from algohns.core.data_providers import get_market_data
from algohns.modules.backtest_suite import Backtester, PortfolioOptimizer
from algohns.ui import dependency_notice, header

header(
    "Advanced Backtesting & Portfolio Optimization",
    "Max Sharpe · Min Variance · Risk Parity · Black-Litterman — with full risk metrics.",
    badge="Module 3",
)

c1, c2, c3 = st.columns([2, 1, 1])
tickers = c1.text_input("Universe (space/comma separated)", value="AAPL MSFT NVDA AMZN GOOGL JPM XOM")
period = c2.selectbox("History", ["1y", "2y", "5y", "10y", "max"], index=2)
method = c3.selectbox(
    "Optimizer",
    ["max_sharpe", "min_volatility", "risk_parity", "equal_weight", "black_litterman"],
)
c4, c5 = st.columns(2)
benchmark = c4.text_input("Benchmark", value="SPY")
rebalance = c5.selectbox("Rebalance", ["Q", "M", "Y", "none"], index=0)

if st.button("Run optimization & backtest", type="primary"):
    md = get_market_data()
    try:
        with st.spinner("Downloading prices…"):
            prices = md.history(tickers, period=period)
        if prices.empty or prices.shape[1] < 2:
            st.error("Not enough price data. Check the tickers.")
            st.stop()

        optimizer = PortfolioOptimizer(prices)
        weights = optimizer.optimize(method)
        expected = optimizer.expected_performance(weights)

        bench_px = None
        try:
            bench_px = md.history(benchmark, period=period).iloc[:, 0]
        except Exception:  # noqa: BLE001
            pass

        bt = Backtester(prices)
        result = bt.run(weights, rebalance=rebalance, benchmark=bench_px)
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

    st.markdown("**Equity curve**")
    curve = result.equity_curve.rename("Portfolio").to_frame()
    if result.benchmark_curve is not None:
        curve[benchmark.upper()] = result.benchmark_curve
    st.line_chart(curve)

    st.markdown("**Drawdown**")
    st.area_chart(result.drawdown_curve.rename("Drawdown"))

    with st.expander("Full metrics + VaR / CVaR"):
        st.json(result.summary())
