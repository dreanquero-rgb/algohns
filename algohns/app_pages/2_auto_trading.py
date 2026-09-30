"""Streamlit page — Module 2: Alpaca Auto-Trading + Risk Profiling.

Flow: the investor answers a risk questionnaire → gets a profile and a suggested
strategic allocation → backtests it (integrated here, separate from Module 3) →
applies/rebalances it on the Alpaca **paper (demo)** account.
"""
from __future__ import annotations

import traceback

import numpy as np
import pandas as pd
import streamlit as st

from algohns.config import get_settings
from algohns.core.data_providers import get_market_data
from algohns.core.persistence import clear_state, load_state, save_state
from algohns.modules.alpaca_execution import AlpacaExecutionEngine, OrderTicket
from algohns.modules.backtest_suite import Backtester, compute_metrics
from algohns.modules.risk_profile import ASSET_PROXIES, QUESTIONS, compute_profile
from algohns.modules import alpaca_execution as ae_mod
from algohns.modules import risk_profile as rp_mod
from algohns.modules import strategy_lab as sl
from algohns import charts as ch
from algohns.ui import code_editor, code_panel, dependency_notice, header, paper_lock_banner


def _show_alpaca_error(prefix: str, exc: Exception) -> None:
    """Surface an Alpaca failure with its *type*, not just its message.

    A bare ``str(exc)`` hides what actually broke — an auth failure, a network
    error and a parsing bug all look different but read alike without the class
    name. The type plus the full traceback (in an expander) is what makes a
    remote problem diagnosable.
    """
    st.error(f"{prefix}: {type(exc).__name__}: {exc}")
    msg = str(exc)
    if "40310000" in msg or "rejected by user request" in msg:
        st.warning(
            "🚫 **Trading is suspended on this Alpaca account** — the "
            "`suspend_trade` flag is on, so every new order is rejected "
            "(this is an account switch, not an app bug). Use the "
            "**⚙️ Account trading status → Re-enable trading** button at the top "
            "of this tab (or turn off *Suspend trading* in the Alpaca dashboard), "
            "then retry the order."
        )
    elif type(exc).__name__ == "APIError":
        st.caption("Alpaca rejected the request. Most common causes: the Key ID "
                   "and Secret are swapped, or these are **live** keys while the "
                   "app is paper-only (generate keys with the *Paper* toggle on).")
    with st.expander("Technical details (copy this if it needs reporting)"):
        st.code("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                language="text")


def _safe_table(df: pd.DataFrame) -> None:
    """Render a dataframe of Alpaca objects without letting it crash the page.

    Alpaca models come back with enums, UUIDs, datetimes and nested legs. Fed
    straight to ``st.dataframe`` those object columns hit Streamlit's Arrow
    type-inference, which on Python 3.14 calls ``ast.parse`` on a cell and
    raises a SyntaxError that took down the whole page. So every object column
    is coerced to a plain scalar (numbers/bools/None kept, everything else
    stringified) *before* rendering — proactively, not by catching after the
    fact — with a stringified-table fallback as a last resort.
    """
    safe = df.copy()
    for col in safe.columns:
        if safe[col].dtype == object:
            safe[col] = safe[col].map(
                lambda v: v if isinstance(v, (int, float, bool)) or v is None else str(v)
            )
    try:
        st.dataframe(safe, width="stretch", hide_index=True)
    except Exception:  # noqa: BLE001
        st.table(safe.astype(str))


def _safe_json(obj) -> None:
    """Show a JSON blob of Alpaca objects safely.

    Same problem as the tables: an order/account dict can hold UUIDs, datetimes
    and enums that are not JSON-serialisable. Round-trip through json with a str
    fallback so the display can never raise.
    """
    import json

    try:
        st.json(json.loads(json.dumps(obj, default=str)))
    except Exception:  # noqa: BLE001
        st.write(obj)

header(
    "Alpaca Auto-Trading & Risk Profiling",
    "Questionnaire → risk profile → allocation → backtest → paper execution.",
    badge="Module 2",
)
paper_lock_banner()
settings = get_settings()


# --- Persistence: fill the questionnaire once, keep it across restarts -------
# session_state is per-session and lost on restart; these hydrate it from disk
# on first load so a saved risk profile and the chosen strategy survive a
# restart (see algohns.core.persistence).
def _hydrate_saved_state() -> None:
    if "risk_profile" not in st.session_state:
        saved = load_state("risk_profile")
        if saved and isinstance(saved.get("answers"), dict):
            try:
                st.session_state["risk_profile"] = compute_profile(
                    saved["answers"], preferences=saved.get("preferences") or [])
                st.session_state["saved_risk_answers"] = saved["answers"]
                st.session_state["saved_risk_prefs"] = saved.get("preferences") or []
            except Exception:  # noqa: BLE001 - a stale blob must not break the page
                pass
    if "risk_profile_override" not in st.session_state:
        saved = load_state("active_strategy")
        if saved and isinstance(saved.get("weights"), dict) and saved["weights"]:
            st.session_state["risk_profile_override"] = {
                str(k): float(v) for k, v in saved["weights"].items()}


_hydrate_saved_state()


def _persist_strategy(weights: dict) -> None:
    """Make the Strategy Lab's choice the active, saved strategy."""
    clean = {str(k): float(v) for k, v in weights.items()}
    st.session_state["risk_profile_override"] = clean
    save_state("active_strategy", {"weights": clean})


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
    st.caption(
        "Fill this once — the answers are **saved to disk and reloaded on every "
        "restart**, so you never re-answer unless you want to. Change anything "
        "and recompute to update it.")

    saved_answers = st.session_state.get("saved_risk_answers", {})
    saved_prefs = st.session_state.get("saved_risk_prefs", [])
    with st.form("risk"):
        answers: dict[str, int] = {}
        for q in QUESTIONS:
            labels = [a[0] for a in q.answers]
            val_to_label = {v: l for l, v in q.answers}
            saved_val = saved_answers.get(q.key)
            idx = (labels.index(val_to_label[saved_val])
                   if saved_val in val_to_label else 0)
            choice = st.radio(q.text, labels, horizontal=True, index=idx,
                              key=f"q_{q.key}")
            answers[q.key] = dict(q.answers)[choice]
        st.markdown("**Where would you like to tilt?** (optional)")
        prefs = st.multiselect(
            "Preferred asset classes", list(ASSET_PROXIES.keys()),
            default=[p for p in saved_prefs if p in ASSET_PROXIES],
            format_func=lambda k: f"{k} ({ASSET_PROXIES[k]})")
        go = st.form_submit_button("Compute & save my profile", type="primary")

    if go:
        profile = compute_profile(answers, preferences=prefs)
        st.session_state["risk_profile"] = profile
        st.session_state["saved_risk_answers"] = answers
        st.session_state["saved_risk_prefs"] = prefs
        if save_state("risk_profile", {"answers": answers, "preferences": prefs}):
            st.toast("Risk profile saved — it will reload automatically next time.")

    if st.session_state.get("risk_profile") is not None:
        if st.button("🗑️ Clear saved profile"):
            clear_state("risk_profile")
            for k in ("risk_profile", "saved_risk_answers", "saved_risk_prefs"):
                st.session_state.pop(k, None)
            st.rerun()

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

    if settings.alpaca_configured:
        with st.expander("⚙️ Account trading status", expanded=False):
            st.caption(
                "If orders are rejected with **code 40310000 — 'new orders are "
                "rejected by user request'**, trading is suspended on the account. "
                "Re-enable it here and retry.")
            b1, b2 = st.columns(2)
            if b1.button("Check trading status"):
                try:
                    if engine.trade_suspended():
                        st.error("🚫 Trading is **SUSPENDED** on this account "
                                 "(`suspend_trade` is on) — new orders are rejected.")
                    else:
                        st.success("✅ Trading is **enabled** on this account.")
                except Exception as exc:  # noqa: BLE001
                    _show_alpaca_error("Status check failed", exc)
            if b2.button("✅ Re-enable trading", type="primary"):
                try:
                    res = engine.set_trade_suspended(False)
                    st.success("Trading re-enabled (`suspend_trade` turned off). "
                               "Retry your order now.")
                    _safe_json(res)
                except Exception as exc:  # noqa: BLE001
                    _show_alpaca_error("Could not re-enable trading", exc)

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
        override = st.session_state.get("risk_profile_override")
        profile = st.session_state.get("risk_profile")
        weights = dict(override) if override else (
            dict(profile.ticker_allocation) if profile else None)

        if not weights:
            st.info("Compute a risk profile (tab 1), or build weights in the "
                    "**Strategy Lab** and send them here.")
        else:
            if override:
                st.caption("Using the **active strategy** from the Strategy Lab "
                           "(saved across restarts).")
                if st.button("↩ Use my risk-profile allocation instead"):
                    st.session_state.pop("risk_profile_override", None)
                    clear_state("active_strategy")
                    st.rerun()
            elif profile:
                st.caption(f"Using your **{profile.label}** risk-profile allocation.")

            st.write("Target allocation:")
            st.json(weights)
            close_untracked = st.checkbox(
                "Also sell holdings that are not in the target (full rebalance)",
                value=False,
                help="Off: only trade the target symbols. On: also liquidate any "
                     "position not in the target so the account matches the "
                     "allocation exactly.",
            )

            c1, c2 = st.columns(2)
            do_preview = c1.button("🔍 Preview plan (no orders sent)",
                                   disabled=not settings.alpaca_configured)
            do_send = c2.button("🚀 Send orders to Alpaca (PAPER)", type="primary",
                                disabled=not settings.alpaca_configured)

            if not settings.alpaca_configured:
                st.warning("Set ALPACA_API_KEY / ALPACA_SECRET_KEY to enable trading.")

            if do_preview or do_send:
                try:
                    plan = engine.rebalance_to_weights(
                        weights, dry_run=not do_send,
                        close_untracked=close_untracked)
                    if not plan:
                        st.info("Already at target — no trades needed.")
                    else:
                        _safe_table(pd.DataFrame(plan))
                        if do_send:
                            sent = [p for p in plan if isinstance(p.get("result"), dict)]
                            st.success(
                                f"✅ {len(sent)} order(s) sent to the Alpaca paper "
                                "account. Open the **Journal** tab to watch them fill.")
                            confirm = [
                                {"symbol": p["symbol"], "side": p["side"],
                                 "order_id": p["result"].get("id"),
                                 "status": p["result"].get("status")}
                                for p in sent
                            ]
                            if confirm:
                                _safe_table(pd.DataFrame(confirm))
                        else:
                            st.caption("Nothing was sent — press **Send orders** to "
                                       "execute this plan on the paper account.")
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
                _safe_json(engine.preview_order(ticket))
            except Exception as exc:  # noqa: BLE001
                _show_alpaca_error("Preview failed", exc)
        if execute and settings.alpaca_configured:
            try:
                result = engine.submit_order(ticket)
                st.success("Order submitted (paper).")
                _safe_json(result)
            except Exception as exc:  # noqa: BLE001
                _show_alpaca_error("Order failed", exc)
        k1, k2 = st.columns(2)
        if k1.button("🛑 Cancel all orders"):
            try:
                _safe_json(engine.cancel_all())
            except Exception as exc:  # noqa: BLE001
                _show_alpaca_error("Cancel failed", exc)
        if k2.button("🧯 Close all positions"):
            try:
                _safe_json(engine.close_all_positions())
            except Exception as exc:  # noqa: BLE001
                _show_alpaca_error("Close-all failed", exc)

    with sub[3]:
        if st.button("Load order journal", disabled=not settings.alpaca_configured):
            try:
                orders = engine.list_orders(status="all", limit=50)
                if orders:
                    _safe_table(pd.DataFrame(orders))
                else:
                    st.info("No orders.")
            except Exception as exc:  # noqa: BLE001
                _show_alpaca_error("Journal error", exc)

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

            if st.button("Set as active strategy (→ Paper Trading)", type="primary"):
                _persist_strategy(weights)
                st.success(f"{len(weights)} target weights are now the **active "
                           "strategy** (saved — it reloads after a restart). "
                           "Apply it in the Paper Trading tab.")
                st.rerun()   # full-app rerun so Paper Trading sees the handoff

            st.divider()
            st.markdown("**6 · Edit & run the strategy yourself**")
            seed = (
                "# `universe` (a DataFrame) plus screen_universe / build_weights /\n"
                "# ScreenCriteria and pd / np are already available — no imports.\n"
                "# Edit the rules, set a `weights` dict, then press Run.\n\n"
                "criteria = ScreenCriteria(\n"
                "    beta_max=1.20,\n"
                "    market_cap_min=50e9,\n"
                "    max_volatility=0.45,\n"
                "    max_positions=12,\n"
                ")\n"
                "picks = screen_universe(universe, criteria)\n"
                "weights = build_weights(picks, 'inverse_vol')\n"
                "print(f'{len(picks)} names selected, {len(weights)} weighted')\n"
            )

            def _strategy_ctx():
                return {
                    "universe": universe,
                    "screen_universe": sl.screen_universe,
                    "build_weights": sl.build_weights,
                    "ScreenCriteria": sl.ScreenCriteria,
                    "WEIGHTINGS": sl.WEIGHTINGS,
                    "pd": pd,
                    "np": np,
                }

            def _render_strategy(result):
                if not isinstance(result, dict) or not result:
                    st.warning("`weights` should be a non-empty dict of "
                               "{ticker: weight}.")
                    return
                wser = pd.Series(result, dtype=float).sort_values(ascending=False)
                st.plotly_chart(
                    ch.hbar(wser.index, wser.values * 100,
                            title="Your weights", height=320,
                            value_fmt="{:.1f}", suffix="%"),
                    width="stretch")
                st.session_state["strategy_code_weights"] = {
                    str(k): float(v) for k, v in result.items()}

            code_editor(
                seed, _strategy_ctx, result_var="weights",
                render_result=_render_strategy, key="strategy_lab",
                title="Live strategy editor",
                intro="This runs the same platform functions the app uses. Whatever "
                      "`weights` you produce can be pushed to the paper account below.",
                filename="algohns_strategy_live.py",
            )
            if st.session_state.get("strategy_code_weights"):
                if st.button("Set edited-code weights as active strategy"):
                    _persist_strategy(st.session_state["strategy_code_weights"])
                    st.success("Edited-code weights are now the **active strategy** "
                               "(saved). Apply it in the Paper Trading tab.")
                    st.rerun()


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
