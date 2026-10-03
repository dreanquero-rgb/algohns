"""Streamlit page — Module 3: Universe Explorer + Backtesting & Optimization."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from algohns import charts as ch
from algohns.core.data_providers import get_market_data
from algohns.modules import universe
from algohns.modules import backtest_suite as bt_mod
from algohns.modules.backtest_suite import Backtester, PortfolioOptimizer, compute_metrics
from algohns.modules.reference_data import spx_history
from algohns.ui import code_editor, code_panel, dependency_notice, header

@st.cache_data(ttl=3600, show_spinner=False)
def _universe_options(asset_class: str) -> dict:
    """FinanceDatabase filter values — a fixed, offline dataset, so cache it."""
    return universe.options(asset_class)


@st.cache_data(ttl=1800, show_spinner=False)
def _universe_search(asset_class: str, filters_key: tuple, query: str,
                     limit: int) -> pd.DataFrame:
    return universe.search(asset_class, filters=dict(filters_key), query=query,
                           limit=limit)


header(
    "Universe Explorer + Backtesting & Optimization",
    "300k+ instruments (FinanceDatabase) · Max Sharpe/Min-Var/Risk-Parity/Black-Litterman · history since 1871.",
    badge="Module 3",
)

tab_universe, tab_backtest, tab_history, tab_code = st.tabs(
    ["🌐 Universe Explorer", "🧪 Optimize & Backtest", "🏛️ Long history (real)",
     "🐍 Code"]
)

# =============================================================================
# TAB 1 — UNIVERSE EXPLORER
# =============================================================================
@st.fragment
def universe_explorer_panel() -> None:
    if not universe.available():
        st.warning("FinanceDatabase not installed → `pip install financedatabase`.")
    else:
        c = st.columns([1, 2])
        asset_class = c[0].selectbox("Asset class", universe.ASSET_CLASSES)
        query = c[1].text_input("Search (symbol or name)", "")

        # FinanceDatabase ships 300k+ instruments and takes ~7s to load the
        # first time. Deferring it behind a press keeps the page's first paint
        # instant for everyone who came here for the backtester instead.
        if st.button("📚 Load universe", type="primary", key="load_universe",
                     help="Loads the bundled FinanceDatabase (300k+ instruments). "
                          "A few seconds the first time, instant afterwards."):
            st.session_state["universe_loaded"] = True
        if not st.session_state.get("universe_loaded"):
            st.info("Press **Load universe** to browse the instrument database.")
            return

        opts = _universe_options(asset_class)
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
            results = _universe_search(asset_class, tuple(sorted(filters.items())),
                                       query, 500)
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
                    width="stretch")
                break

        st.dataframe(results, width="stretch", hide_index=True, height=360)
        syms = universe.tickers_from(results)
        chosen = st.multiselect("Select tickers to backtest", syms, default=syms[:8])
        if st.button("➡️ Send selection to backtest", type="primary", disabled=not chosen):
            st.session_state["bt_tickers"] = " ".join(chosen)
            st.success(f"{len(chosen)} tickers sent to the Optimize & Backtest tab.")
            # Full-app rerun so the other tab picks the handoff up; a fragment
            # rerun alone would leave it showing the previous selection.
            st.rerun()

# =============================================================================
# TAB 2 — OPTIMIZE & BACKTEST
# =============================================================================
with tab_universe:
    universe_explorer_panel()

with tab_backtest:
    # Easier than typing tickers: pull them from the confirmed Strategy Lab
    # strategy or the Universe Explorer selection (both land in session_state).
    imp = st.columns([2, 2, 3])
    strat_w = st.session_state.get("risk_profile_override")
    if imp[0].button("⬇️ Import confirmed strategy",
                     disabled=not strat_w,
                     help="Fill the universe from the strategy you confirmed in "
                          "Alpaca Auto-Trading → Strategy Lab."):
        st.session_state["bt_tickers"] = " ".join(sorted(strat_w))
        st.rerun()
    if imp[1].button("⬇️ Import Universe Explorer selection",
                     disabled=not st.session_state.get("bt_tickers"),
                     help="Use the tickers you sent from the Universe Explorer tab."):
        st.rerun()

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

    causal = st.toggle(
        "Walk-forward (causal)", value=True,
        help="Re-optimizes at every rebalance using only the data available "
             "at that date. When turned off, the weights are estimated on the "
             "WHOLE sample and then reapplied to the same sample: the "
             "resulting curve is in-sample and shows the return you would have "
             "had if you had known the optimal weights in advance.",
    )
    if not causal:
        st.warning(
            "**In-sample mode.** The weights are estimated on the entire "
            "period and then reapplied to the same period: the curve "
            "systematically overstates what was actually achievable. Useful "
            "for inspecting the allocation, not for evaluating a strategy."
        )

    if st.button("Run optimization & backtest", type="primary"):
        md = get_market_data()
        src = "stooq" if source.startswith("stooq") else "yfinance"
        try:
            with st.spinner("Downloading prices…"):
                prices = md.history(tickers, period=period, source=src, start=start)
            # Flag tickers that returned no data, but keep going with the rest.
            requested = [t.strip().upper() for t in tickers.replace(",", " ").split()
                         if t.strip()]
            found = {str(c).upper() for c in prices.columns}
            missing = [t for t in requested if t not in found]
            if missing:
                st.warning("No price data for: " + ", ".join(missing)
                           + " — backtesting on the remaining "
                           f"{len(found)} ticker(s).")
            if prices.empty or prices.shape[1] < 2:
                st.error("Not enough price data to backtest (need at least 2 valid "
                         "tickers; the network may be blocked here — works on deploy).")
                st.stop()
            optimizer = PortfolioOptimizer(prices)
            weights = optimizer.optimize(method)
            expected = optimizer.expected_performance(weights)
            bench_px = None
            try:
                bench_px = md.history(benchmark, period=period, source=src, start=start).iloc[:, 0]
            except Exception:  # noqa: BLE001
                pass
            bt = Backtester(prices)
            if causal:
                # Re-optimises at every rebalance on data available then.
                result = Backtester(prices).run_walk_forward(
                    method,
                    rebalance=(rebalance if rebalance != "none" else "Q"),
                    benchmark=bench_px,
                )
            else:
                result = bt.run(weights, rebalance=rebalance, benchmark=bench_px)
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
                        width="stretch")
        st.plotly_chart(ch.area(result.drawdown_curve.rename("Drawdown"),
                                title="Drawdown", negative=True),
                        width="stretch")

        cc = st.columns(2)
        # ---- weights ---------------------------------------------------------
        wser = pd.Series(weights).sort_values(ascending=False)
        with cc[0]:
            st.plotly_chart(
                ch.hbar(wser.index, wser.values * 100, title="Optimized weights",
                        height=340, value_fmt="{:.1f}", suffix="%"),
                width="stretch")
            e = st.columns(3)
            e[0].metric("Exp. return", f"{expected['expected_return']*100:.2f}%")
            e[1].metric("Exp. vol", f"{expected['expected_volatility']*100:.2f}%")
            e[2].metric("Exp. Sharpe", f"{expected['expected_sharpe']:.2f}")
        # ---- correlation matrix ---------------------------------------------
        with cc[1]:
            corr = prices.pct_change().dropna().corr()
            st.plotly_chart(ch.heatmap(corr, title="Correlation matrix", height=340),
                            width="stretch")

        # ---- rolling 1y Sharpe ----------------------------------------------
        rets = result.equity_curve.pct_change().dropna()
        if len(rets) > 260:
            roll = (rets.rolling(252).mean() / rets.rolling(252).std()) * np.sqrt(252)
            st.plotly_chart(ch.line(roll.dropna().rename("Rolling 1y Sharpe").to_frame(),
                                    title="Rolling 1-year Sharpe ratio", height=300),
                            width="stretch")

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
                        width="stretch")

        dd = h["SP500"] / h["SP500"].cummax() - 1
        st.plotly_chart(ch.area(dd.rename("Drawdown"), title="Historical drawdown", negative=True),
                        width="stretch")

        cols = st.columns(2)
        if "PE10" in h and h["PE10"].gt(0).any():
            with cols[0]:
                pe = h.loc[h["PE10"] > 0, ["PE10"]].rename(columns={"PE10": "CAPE (PE10)"})
                st.plotly_chart(ch.line(pe, title="Shiller CAPE ratio", height=300),
                                width="stretch")
        if "Long Interest Rate" in h and h["Long Interest Rate"].gt(0).any():
            with cols[1]:
                ir = h.loc[h["Long Interest Rate"] > 0, ["Long Interest Rate"]].rename(
                    columns={"Long Interest Rate": "Long interest rate %"})
                st.plotly_chart(ch.line(ir, title="Long-term interest rate", height=300),
                                width="stretch")

        # Decade returns — magnitude with polarity
        dec = h["SP500"].resample("10YS").first().pct_change().dropna() * 100
        if not dec.empty:
            st.plotly_chart(
                ch.bar([f"{d.year}s" for d in dec.index], dec.values,
                       title="Return by decade", suffix="%", color_by_sign=True, height=300),
                width="stretch")

# =============================================================================
# TAB 4 — CODE  (the backtester, pulled live from source)
# =============================================================================
with tab_code:
    st.subheader("Edit & run a backtest")
    _bt_seed = (
        "# Backtester, PortfolioOptimizer, compute_metrics, get_market_data and\n"
        "# pd / np are already available — no imports needed. Set `result` to a\n"
        "# BacktestResult to display it.\n\n"
        "prices = get_market_data().history('AAPL MSFT NVDA JPM XOM', period='5y')\n"
        "weights = PortfolioOptimizer(prices).optimize('max_sharpe')\n"
        "result = Backtester(prices).run(weights, rebalance='Q')\n"
        "print(result.metrics.as_dict())\n"
    )

    def _bt_ctx():
        return {
            "Backtester": Backtester,
            "PortfolioOptimizer": PortfolioOptimizer,
            "compute_metrics": compute_metrics,
            "get_market_data": get_market_data,
            "pd": pd,
            "np": np,
        }

    def _render_backtest(result):
        m = getattr(result, "metrics", None)
        if m is None or not hasattr(result, "equity_curve"):
            st.write(result)
            return
        md = m.as_dict()
        k = st.columns(4)
        k[0].metric("CAGR", f"{md['cagr']*100:.2f}%")
        k[1].metric("Sharpe", f"{md['sharpe']:.2f}")
        k[2].metric("Max DD", f"{md['max_drawdown']*100:.2f}%")
        k[3].metric("Volatility", f"{md['annual_volatility']*100:.2f}%")
        st.plotly_chart(
            ch.line(result.equity_curve.rename("Portfolio").to_frame(),
                    title="Equity curve (your code)", height=360),
            width="stretch")

    code_editor(
        _bt_seed, _bt_ctx, result_var="result",
        render_result=_render_backtest, key="backtest",
        title="Live backtest editor",
        intro="Runs the same Backtester and optimizer the app uses. Whatever you "
              "assign to `result` (a BacktestResult) is charted below.",
        filename="algohns_backtest_live.py",
    )

    st.divider()
    st.subheader("Backtest engine source")
    st.caption(
        "The whole backtester, pulled live with `inspect` — what is shown is what "
        "ran. Two things worth pointing a reviewer at: `run_walk_forward` "
        "re-optimises at each rebalance using only data available at that date "
        "(so the equity curve is causal, not fitted in hindsight), and "
        "`compute_metrics` is where CAGR, Sharpe, Sortino, Calmar, drawdown, "
        "alpha/beta and VaR/CVaR are defined."
    )
    code_panel(
        [("Backtester", Backtester),
         ("Walk-forward (causal)", Backtester.run_walk_forward),
         ("Optimizer", PortfolioOptimizer),
         ("Metrics", compute_metrics),
         ("Full module", bt_mod)],
        title="Backtesting & optimization — full source",
        intro="Tabs isolate the pieces; the last tab is the entire module.",
        expanded=True, filename="algohns_backtest.py",
    )
