"""Streamlit page — Module 2: Alpaca Auto-Trading + Risk Profiling.

Flow: the investor answers a risk questionnaire → gets a profile and a suggested
strategic allocation → backtests it (integrated here, separate from Module 3) →
applies/rebalances it on the Alpaca **paper (demo)** account.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from algohns.config import get_settings
from algohns.core.data_providers import get_market_data
from algohns.modules.alpaca_execution import AlpacaExecutionEngine, OrderTicket
from algohns.modules.backtest_suite import Backtester, compute_metrics
from algohns.modules.risk_profile import ASSET_PROXIES, QUESTIONS, compute_profile
from algohns.ui import dependency_notice, header, paper_lock_banner

header(
    "Alpaca Auto-Trading & Risk Profiling",
    "Questionario → profilo di rischio → allocazione → backtest → esecuzione paper.",
    badge="Module 2",
)
paper_lock_banner()
settings = get_settings()

tabs = st.tabs(["🧭 Risk Profile", "🧪 Profile Backtest", "🤖 Paper Trading", "🛠️ Worker"])

# =============================================================================
# TAB 1 — RISK QUESTIONNAIRE
# =============================================================================
with tabs[0]:
    st.subheader("Investor risk questionnaire")
    with st.form("risk"):
        answers: dict[str, int] = {}
        for q in QUESTIONS:
            labels = [a[0] for a in q.answers]
            choice = st.radio(q.text, labels, horizontal=True, key=f"q_{q.key}")
            answers[q.key] = dict(q.answers)[choice]
        st.markdown("**Where would you like to tilt?** (optional)")
        prefs = st.multiselect("Preferred asset classes", list(ASSET_PROXIES.keys()),
                               format_func=lambda k: f"{k} ({ASSET_PROXIES[k]})")
        go = st.form_submit_button("Compute my profile", type="primary")

    if go:
        profile = compute_profile(answers, preferences=prefs)
        st.session_state["risk_profile"] = profile

    profile = st.session_state.get("risk_profile")
    if profile:
        c = st.columns(3)
        c[0].metric("Risk score", f"{profile.score:.0f}/100")
        c[1].metric("Profile", profile.label)
        c[2].metric("Holdings", len(profile.ticker_allocation))
        st.caption(profile.description)

        alloc_df = pd.DataFrame(
            [{"Asset class": k, "ETF": ASSET_PROXIES[k], "Weight %": round(v * 100, 1)}
             for k, v in profile.allocation.items()]
        ).sort_values("Weight %", ascending=False)
        cc = st.columns([1, 1])
        cc[0].dataframe(alloc_df, use_container_width=True, hide_index=True)
        cc[1].bar_chart(alloc_df.set_index("ETF")["Weight %"])
        st.success("Profile saved — use it in the **Profile Backtest** and **Paper Trading** tabs.")

# =============================================================================
# TAB 2 — INTEGRATED BACKTEST (separate from Module 3)
# =============================================================================
with tabs[1]:
    profile = st.session_state.get("risk_profile")
    if not profile:
        st.info("Compute your risk profile first (tab 1).")
    else:
        st.subheader(f"Backtest — {profile.label} allocation")
        period = st.selectbox("History", ["1y", "3y", "5y", "10y"], index=2)
        if st.button("Run integrated backtest", type="primary"):
            md = get_market_data()
            tickers = list(profile.ticker_allocation.keys())
            try:
                with st.spinner("Downloading prices…"):
                    prices = md.history(tickers, period=period)
                if prices.empty:
                    st.warning("No price data returned (network may be blocked here; works on deploy).")
                else:
                    weights = {t: w for t, w in profile.ticker_allocation.items() if t in prices.columns}
                    res = Backtester(prices).run(weights, rebalance="Q")
                    m = res.metrics.as_dict()
                    k = st.columns(4)
                    k[0].metric("CAGR", f"{m['cagr']*100:.2f}%")
                    k[1].metric("Sharpe", f"{m['sharpe']:.2f}")
                    k[2].metric("Max DD", f"{m['max_drawdown']*100:.2f}%")
                    k[3].metric("Volatility", f"{m['annual_volatility']*100:.2f}%")
                    st.line_chart(res.equity_curve.rename("Portfolio"))
                    st.area_chart(res.drawdown_curve.rename("Drawdown"))
            except Exception as exc:  # noqa: BLE001
                dependency_notice(exc)

# =============================================================================
# TAB 3 — PAPER TRADING
# =============================================================================
with tabs[2]:
    if not settings.alpaca_configured:
        st.warning("Set ALPACA_API_KEY / ALPACA_SECRET_KEY to trade on the paper account.")
    try:
        engine = AlpacaExecutionEngine()
    except Exception as exc:  # noqa: BLE001
        dependency_notice(exc)
        st.stop()

    sub = st.tabs(["Portfolio", "Apply profile", "Order ticket", "Journal"])

    with sub[0]:
        if st.button("Refresh snapshot", disabled=not settings.alpaca_configured):
            try:
                snap = engine.portfolio_snapshot()
                c = st.columns(3)
                c[0].metric("Equity", f"${snap['equity']:,.2f}")
                c[1].metric("Cash", f"${snap['cash']:,.2f}")
                c[2].metric("Buying power", f"${snap['buying_power']:,.2f}")
                st.dataframe(pd.DataFrame(snap["positions"]), use_container_width=True, hide_index=True) \
                    if snap["positions"] else st.info("No open positions.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Alpaca error: {exc}")

    with sub[1]:
        profile = st.session_state.get("risk_profile")
        if not profile:
            st.info("Compute your risk profile first (tab 1).")
        else:
            st.write("Target allocation from your profile:")
            st.json(profile.ticker_allocation)
            dry = st.toggle("Dry-run (plan only)", value=True)
            if st.button("Rebalance paper account to profile", type="primary",
                         disabled=not settings.alpaca_configured):
                try:
                    plan = engine.rebalance_to_weights(profile.ticker_allocation, dry_run=dry)
                    st.dataframe(pd.DataFrame(plan), use_container_width=True, hide_index=True) \
                        if plan else st.info("Already at target — no trades needed.")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Rebalance error: {exc}")

    with sub[2]:
        with st.form("order"):
            cc = st.columns(3)
            symbol = cc[0].text_input("Symbol", value="SPY")
            side = cc[1].selectbox("Side", ["buy", "sell"])
            otype = cc[2].selectbox("Type", ["market", "limit"])
            cc2 = st.columns(3)
            qty = cc2[0].number_input("Qty", value=1.0, min_value=0.0, step=1.0)
            notional = cc2[1].number_input("Notional $ (0=use qty)", value=0.0, min_value=0.0, step=50.0)
            limit_price = cc2[2].number_input("Limit price", value=0.0, min_value=0.0, step=0.5)
            preview = st.form_submit_button("Preview")
            execute = st.form_submit_button("Execute (PAPER)", type="primary")
        ticket = OrderTicket(symbol=symbol.upper(), qty=(qty or None) if notional == 0 else None,
                             notional=notional or None, side=side, type=otype,
                             limit_price=limit_price or None)
        if preview:
            st.json(engine.preview_order(ticket))
        if execute and settings.alpaca_configured:
            try:
                st.success("Order submitted (paper).")
                st.json(engine.submit_order(ticket))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Order failed: {exc}")
        k1, k2 = st.columns(2)
        if k1.button("🛑 Cancel all orders"):
            st.json(engine.cancel_all())
        if k2.button("🧯 Close all positions"):
            st.json(engine.close_all_positions())

    with sub[3]:
        if st.button("Load order journal", disabled=not settings.alpaca_configured):
            orders = engine.list_orders(status="all", limit=50)
            st.dataframe(pd.DataFrame(orders), use_container_width=True, hide_index=True) \
                if orders else st.info("No orders.")

# =============================================================================
# TAB 4 — WORKER
# =============================================================================
with tabs[3]:
    st.markdown(
        "Background execution so the strategy keeps running with the browser closed:\n\n"
        "```bash\n"
        "celery -A algohns.workers.celery_app.app worker --loglevel=info\n"
        "celery -A algohns.workers.celery_app.app beat   --loglevel=info\n"
        "```\n"
        "Broker-less alternative (laptop):\n"
        "```python\n"
        "from algohns.workers.tasks import InlineScheduler\n"
        "InlineScheduler().start(sync_interval_seconds=300)\n"
        "```"
    )
    st.caption(f"Broker: {settings.celery_broker} · Backend: {settings.celery_backend}")
