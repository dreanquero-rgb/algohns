"""Streamlit page — Module 2: Alpaca Auto-Trading + Risk Profiling.

Flow: the investor answers a risk questionnaire → gets a profile and a suggested
strategic allocation → backtests it (integrated here, separate from Module 3) →
applies/rebalances it on the Alpaca **paper (demo)** account.
"""
from __future__ import annotations

import traceback

import pandas as pd
import streamlit as st

from algohns.config import get_settings
from algohns.core.data_providers import get_market_data
from algohns.modules.alpaca_execution import AlpacaExecutionEngine, OrderTicket
from algohns.modules.backtest_suite import Backtester, compute_metrics
from algohns.modules.risk_profile import ASSET_PROXIES, QUESTIONS, compute_profile
from algohns.modules import alpaca_execution as ae_mod
from algohns.modules import risk_profile as rp_mod
from algohns.modules import strategy_lab as sl
from algohns import charts as ch
from algohns.ui import code_panel, dependency_notice, header, paper_lock_banner


def _show_alpaca_error(prefix: str, exc: Exception) -> None:
    """Surface an Alpaca failure with its *type*, not just its message.

    A bare ``str(exc)`` hides what actually broke — an auth failure, a network
    error and a parsing bug all look different but read alike without the class
    name. The type plus the full traceback (in an expander) is what makes a
    remote problem diagnosable.
    """
    st.error(f"{prefix}: {type(exc).__name__}: {exc}")
    if type(exc).__name__ == "APIError":
        st.caption("Alpaca rejected the request. Most common causes: the Key ID "
                   "and Secret are swapped, or these are **live** keys while the "
                   "app is paper-only (generate keys with the *Paper* toggle on).")
    with st.expander("Technical details (copy this if it needs reporting)"):
        st.code("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                language="text")


def _safe_table(df: pd.DataFrame) -> None:
    """Render a dataframe, falling back to a string table if Arrow can't encode it.

    Streamlit serialises dataframes through pyarrow, which can choke on mixed
    object columns; that must not take down the whole panel, so on any failure
    we show a stringified table instead of raising.
    """
    try:
        st.dataframe(df, width="stretch", hide_index=True)
    except Exception:  # noqa: BLE001
        st.table(df.astype(str))

header(
    "Alpaca Auto-Trading & Risk Profiling",
    "Questionnaire → risk profile → allocation → backtest → paper execution.",
    badge="Module 2",
)
paper_lock_banner()
settings = get_settings()

@st.cache_data(ttl=3600, show_spinner=False)
def _lab_universe():
    """Screening universe with real betas/caps — a fixed dataset, so cache it."""
    return sl.demo_universe()


tabs = st.tabs([
    "🧭 Risk Profile", "🔬 Strategy Lab", "🧪 Profile Backtest",
    "🤖 Paper Trading", "🛠️ Worker", "🐍 Code",
])

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
        with cc[0]:
            st.plotly_chart(
                ch.donut(alloc_df["ETF"], alloc_df["Weight %"] / 100,
                         title="Strategic allocation", center=profile.label),
                width="stretch")
        with cc[1]:
            st.plotly_chart(
                ch.hbar(alloc_df["Asset class"], alloc_df["Weight %"],
                        title="Weight by asset class", height=340,
                        value_fmt="{:.1f}", suffix="%"),
                width="stretch")
        st.dataframe(alloc_df, width="stretch", hide_index=True)
        st.success("Profile saved — use it in the **Profile Backtest** and **Paper Trading** tabs.")

# =============================================================================
# TAB 2 — INTEGRATED BACKTEST (separate from Module 3)
# =============================================================================
with tabs[2]:
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
                    st.plotly_chart(
                        ch.line(res.equity_curve.rename("Portfolio").to_frame(),
                                title=f"Equity curve — {profile.label} allocation"),
                        width="stretch")
                    st.plotly_chart(
                        ch.area(res.drawdown_curve.rename("Drawdown"),
                                title="Drawdown", negative=True),
                        width="stretch")
            except Exception as exc:  # noqa: BLE001
                dependency_notice(exc)

# =============================================================================
# TAB 3 — PAPER TRADING
# =============================================================================
with tabs[3]:
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
                if snap["positions"]:
                    _safe_table(pd.DataFrame(snap["positions"]))
                else:
                    st.info("No open positions.")
            except Exception as exc:  # noqa: BLE001
                _show_alpaca_error("Alpaca error", exc)

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
                    if plan:
                        _safe_table(pd.DataFrame(plan))
                    else:
                        st.info("Already at target — no trades needed.")
                except Exception as exc:  # noqa: BLE001
                    _show_alpaca_error("Rebalance error", exc)

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
            try:
                st.json(engine.preview_order(ticket))
            except Exception as exc:  # noqa: BLE001
                _show_alpaca_error("Preview failed", exc)
        if execute and settings.alpaca_configured:
            try:
                result = engine.submit_order(ticket)
                st.success("Order submitted (paper).")
                st.json(result)
            except Exception as exc:  # noqa: BLE001
                _show_alpaca_error("Order failed", exc)
        k1, k2 = st.columns(2)
        if k1.button("🛑 Cancel all orders"):
            st.json(engine.cancel_all())
        if k2.button("🧯 Close all positions"):
            st.json(engine.close_all_positions())

    with sub[3]:
        if st.button("Load order journal", disabled=not settings.alpaca_configured):
            orders = engine.list_orders(status="all", limit=50)
            st.dataframe(pd.DataFrame(orders), width="stretch", hide_index=True) \
                if orders else st.info("No orders.")

# =============================================================================
# TAB 4 — WORKER
# =============================================================================
with tabs[4]:
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

# =============================================================================
# TAB 2 — STRATEGY LAB  (rules -> screened universe -> weights -> Python)
# =============================================================================
@st.fragment
def strategy_lab_panel() -> None:
    st.subheader("Strategy Lab — state the rules, read the code")
    st.caption(
        "The questionnaire gives a risk profile; this tab is where that becomes "
        "an explicit, inspectable rule set. The Python at the bottom is generated "
        "from these exact rules and calls the same platform functions the app "
        "runs, so it reproduces the allocation rather than approximating it."
    )

    universe = _lab_universe()
    if universe.empty:
        st.warning("World universe unavailable, so the screener has no input.")
    else:
        sectors = sorted(universe["sector"].dropna().unique().tolist())
        countries = sorted(universe["country"].dropna().unique().tolist())

        st.markdown("**1 · Investable-universe rules**")
        r1 = st.columns(4)
        beta_band = r1[0].slider("Beta band", 0.0, 2.5, (0.0, 1.30), 0.05,
                                 help="Sensitivity to the market factor. Below 1.0 "
                                      "is defensive.")
        cap_min_bn = r1[1].number_input("Min market cap ($bn)", 0.0, 5000.0, 50.0, 10.0)
        max_vol = r1[2].slider("Max volatility", 0.05, 1.00, 0.45, 0.01,
                               help="Annualised. Approximated as beta x 16% market vol.")
        max_pos = r1[3].number_input("Max positions", 1, 50, 12, 1)

        r2 = st.columns(2)
        keep_sectors = r2[0].multiselect("Only these sectors (empty = all)", sectors)
        drop_sectors = r2[1].multiselect("Exclude these sectors",
                                         [s for s in sectors if s not in keep_sectors])
        r3 = st.columns([2, 3])
        keep_countries = r3[0].multiselect("Only these domiciles (empty = all)", countries)
        expression = r3[1].text_input(
            "Advanced filter (optional pandas expression)",
            placeholder="beta < 1.1 & market_cap > 2e11",
            help="One boolean expression over the screening columns. Evaluated "
                 "with no builtins and no attribute access — it can filter rows "
                 "and nothing else.",
        )

        st.markdown("**2 · Weighting**")
        weighting = st.selectbox("Weighting scheme", list(sl.WEIGHTINGS.keys()),
                                 format_func=lambda k: f"{k} — {sl.WEIGHTINGS[k]}")
        rebalance_sl = st.selectbox("Rebalance", ["Q", "M", "Y", "none"], index=0,
                                    key="sl_rebal")

        try:
            criteria = sl.ScreenCriteria(
                beta_min=beta_band[0] or None,
                beta_max=beta_band[1],
                market_cap_min=cap_min_bn * 1e9 if cap_min_bn else None,
                max_volatility=max_vol,
                sectors=tuple(keep_sectors),
                exclude_sectors=tuple(drop_sectors),
                countries=tuple(keep_countries),
                max_positions=int(max_pos),
                expression=expression.strip(),
            )
            picks = sl.screen_universe(universe, criteria)
        except sl.FilterExpressionError as exc:
            st.error(f"Filter expression rejected: {exc}")
            picks = universe.iloc[0:0]
        except ValueError as exc:
            st.error(f"Contradictory rules: {exc}")
            picks = universe.iloc[0:0]

        st.markdown("**3 · The screen**")
        k = st.columns(4)
        k[0].metric("Universe", len(universe))
        k[1].metric("Passing the screen", len(picks))
        if not picks.empty:
            k[2].metric("Avg beta", f"{pd.to_numeric(picks['beta']).mean():.2f}")
            k[3].metric("Avg volatility", f"{pd.to_numeric(picks['volatility']).mean():.1%}")

        if picks.empty:
            st.info("No instrument satisfies these rules — loosen a constraint.")
        else:
            weights = sl.build_weights(picks, weighting)
            st.session_state["lab_weights"] = weights
            shown = picks.copy()
            shown["weight %"] = [round(weights.get(str(t), 0.0) * 100, 2)
                                 for t in shown["ticker"]]
            shown["market_cap ($bn)"] = (pd.to_numeric(shown["market_cap"],
                                                       errors="coerce") / 1e9).round(1)
            st.dataframe(
                shown[["ticker", "name", "sector", "country", "beta",
                       "volatility", "market_cap ($bn)", "weight %"]]
                .sort_values("weight %", ascending=False),
                width="stretch", hide_index=True, height=300,
            )

            cc = st.columns(2)
            wser = pd.Series(weights).sort_values(ascending=False)
            with cc[0]:
                st.plotly_chart(
                    ch.hbar(wser.index, wser.values * 100,
                            title=f"Target weights — {weighting}", height=340,
                            value_fmt="{:.1f}", suffix="%"),
                    width="stretch")
            with cc[1]:
                st.plotly_chart(
                    ch.scatter(picks.assign(
                        weight=[weights.get(str(t), 0.0) * 100 for t in picks["ticker"]]),
                        x="beta", y="volatility", label="ticker", group="sector",
                        title="Risk profile of the screen", xtitle="Beta",
                        ytitle="Volatility", suffix="", height=340),
                    width="stretch")

            st.markdown("**4 · Rules in force**")
            st.markdown("\n".join(f"- {r}" for r in criteria.describe()))

            st.markdown("**5 · The generated strategy**")
            profile = st.session_state.get("risk_profile")
            code = sl.generate_strategy_code(
                criteria, weighting, rebalance_sl,
                profile=(profile.label if profile else "custom"),
            )
            st.caption(
                "Generated from the rules above. Download it and run it against "
                "the repo to reproduce this allocation, or edit it freely there — "
                "the app itself never executes uploaded code, which is why a "
                "public deployment stays safe."
            )
            st.code(code, language="python", line_numbers=True)
            st.download_button("⬇️ Download strategy.py", code.encode(),
                               file_name="algohns_strategy.py", mime="text/x-python")

            if st.button("Send these weights to Paper Trading", type="primary"):
                st.session_state["risk_profile_override"] = weights
                st.success(f"{len(weights)} target weights staged for the paper account.")
                st.rerun()   # full-app rerun so Paper Trading sees the handoff


with tabs[1]:
    strategy_lab_panel()


# =============================================================================
# TAB 5 — CODE  (the engine, pulled live from source)
# =============================================================================
with tabs[5]:
    st.subheader("Engine source")
    st.caption(
        "Everything the auto-trader runs, pulled live with `inspect` so the code "
        "shown is the code that executed. Execution is Alpaca **paper** only — "
        "real-money trading is locked platform-wide."
    )
    code_panel(
        [("Strategy lab", sl),
         ("Risk questionnaire", rp_mod),
         ("Alpaca execution", ae_mod)],
        title="Auto-trading engine — full source",
        intro="Screening and weighting rules, the risk questionnaire, and the "
              "Alpaca execution façade with its paper-trading lock.",
        expanded=True, filename="algohns_autotrader.py",
    )
