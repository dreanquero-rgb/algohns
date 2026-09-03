"""Streamlit page — Module 2: Alpaca Asynchronous Auto-Trading Engine."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from algohns.config import get_settings
from algohns.modules.alpaca_execution import AlpacaExecutionEngine, OrderTicket
from algohns.ui import dependency_notice, header, paper_lock_banner

header(
    "Alpaca Asynchronous Engine",
    "Paper-only order execution and background portfolio synchronisation.",
    badge="Module 2",
)
paper_lock_banner()

settings = get_settings()
if not settings.alpaca_configured:
    st.warning("Alpaca keys not configured. Set ALPACA_API_KEY and ALPACA_SECRET_KEY (see .env.example).")

try:
    engine = AlpacaExecutionEngine()
except Exception as exc:  # noqa: BLE001
    dependency_notice(exc)
    st.stop()

tab_portfolio, tab_order, tab_rebalance, tab_worker = st.tabs(
    ["📂 Portfolio", "🧾 Order ticket", "⚖️ Rebalance", "🛠️ Background worker"]
)

with tab_portfolio:
    if st.button("Refresh snapshot", type="primary", disabled=not settings.alpaca_configured):
        try:
            snap = engine.portfolio_snapshot()
            c = st.columns(3)
            c[0].metric("Equity", f"${snap['equity']:,.2f}")
            c[1].metric("Cash", f"${snap['cash']:,.2f}")
            c[2].metric("Buying power", f"${snap['buying_power']:,.2f}")
            if snap["positions"]:
                st.dataframe(pd.DataFrame(snap["positions"]), use_container_width=True, hide_index=True)
            else:
                st.info("No open positions.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Alpaca error: {exc}")

with tab_order:
    with st.form("order"):
        c1, c2, c3 = st.columns(3)
        symbol = c1.text_input("Symbol", value="AAPL")
        side = c2.selectbox("Side", ["buy", "sell"])
        otype = c3.selectbox("Type", ["market", "limit"])
        c4, c5, c6 = st.columns(3)
        qty = c4.number_input("Qty (blank = use notional)", value=1.0, min_value=0.0, step=1.0)
        notional = c5.number_input("Notional $ (0 = use qty)", value=0.0, min_value=0.0, step=50.0)
        limit_price = c6.number_input("Limit price", value=0.0, min_value=0.0, step=0.5)
        preview = st.form_submit_button("Preview")
        execute = st.form_submit_button("Execute (PAPER)", type="primary")

    ticket = OrderTicket(
        symbol=symbol.upper(),
        qty=qty or None if notional == 0 else None,
        notional=notional or None,
        side=side, type=otype,
        limit_price=limit_price or None,
    )
    if preview:
        st.json(engine.preview_order(ticket))
    if execute:
        if not settings.alpaca_configured:
            st.error("Configure Alpaca keys first.")
        else:
            try:
                st.success("Order submitted (paper).")
                st.json(engine.submit_order(ticket))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Order failed: {exc}")

    st.divider()
    k1, k2 = st.columns(2)
    if k1.button("🛑 Cancel all orders"):
        st.json(engine.cancel_all())
    if k2.button("🧯 Close all positions (kill switch)"):
        st.json(engine.close_all_positions())

with tab_rebalance:
    st.caption("Enter target weights as `SYMBOL=weight` per line (weights auto-normalised).")
    raw = st.text_area("Target weights", value="AAPL=0.4\nMSFT=0.35\nNVDA=0.25", height=120)
    weights = {}
    for line in raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            try:
                weights[k.strip().upper()] = float(v)
            except ValueError:
                pass
    dry = st.toggle("Dry-run (plan only, no execution)", value=True)
    if st.button("Generate rebalance plan", type="primary"):
        try:
            plan = engine.rebalance_to_weights(weights, dry_run=dry)
            st.dataframe(pd.DataFrame(plan), use_container_width=True, hide_index=True) if plan \
                else st.info("Portfolio already at target (no trades needed).")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Rebalance error: {exc}")

with tab_worker:
    st.markdown(
        "Run continuous background execution so strategies keep operating with the browser closed:\n\n"
        "**Celery + Redis (production):**\n"
        "```bash\n"
        "celery -A algohns.workers.celery_app.app worker --loglevel=info\n"
        "celery -A algohns.workers.celery_app.app beat --loglevel=info\n"
        "```\n"
        "**APScheduler (laptop, no broker):**\n"
        "```python\n"
        "from algohns.workers.tasks import InlineScheduler\n"
        "sched = InlineScheduler(); sched.start(sync_interval_seconds=300)\n"
        "```"
    )
    st.caption(f"Broker: {settings.celery_broker}  ·  Backend: {settings.celery_backend}")
