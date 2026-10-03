"""Global government-bond-curve panel.

Extracted from the former standalone page so it can live inside the Bond Yield
& Tax page. Rendered as ``render()`` — self-contained, with its own load gate so
it never slows the screener that hosts it: the (possibly networked) curve fit
runs only after the user opens this section and presses Load.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import streamlit as st

from algohns import charts as ch
from algohns.modules import gov_curves as gcv
from algohns.ui import code_editor


@st.cache_data(ttl=1800, show_spinner=False)
def _structures(allow_live: bool, settle: str):
    curves, notes = gcv.load_term_structures(
        allow_live=allow_live, settlement=date.fromisoformat(settle))
    packed = [{
        "code": c.code, "name": c.name, "instrument": c.instrument,
        "source": c.source, "as_of": c.as_of, "n": c.n_instruments,
        "points": c.points, "fit": c.fit,
    } for c in curves]
    return packed, notes


@st.cache_data(ttl=1800, show_spinner=False)
def _history(codes: tuple[str, ...], allow_live: bool):
    return gcv.load_benchmark_history(list(codes), allow_live=allow_live)


def _as_curve(d: dict) -> gcv.CountryCurve:
    return gcv.CountryCurve(d["code"], d["name"], d["instrument"], d["source"],
                            d["points"], d["as_of"], d["n"], d["fit"])


def render() -> None:
    """Render the whole global-curves section (controls + sub-tabs)."""
    st.subheader("Global government bond curves")
    st.caption(
        "BTP · Bund · OAT · Bonos · Gilt · Treasury · JGB on one axis — each a "
        "**Nelson-Siegel fit** to real instrument quotes, not a line through the "
        "nearest two bonds.")

    ctrl = st.columns([1, 1, 1])
    # Live is OFF by default so the tab always loads instantly from committed
    # real snapshots (the Italian curve is bundled). Turning live on adds the
    # multi-country FRED data; if that fetch fails it silently keeps the
    # snapshots, so the tab can never end up showing nothing.
    allow_live = ctrl[0].toggle(
        "Fetch live multi-country data (FRED)", value=False, key="gcv_live",
        help="Off: instant, committed real snapshots. On: also pulls live 10y "
             "benchmarks and the US curve from FRED (slower, needs network).")
    settlement = ctrl[1].date_input("Settlement", value=date.today(),
                                    key="gcv_settle")
    if ctrl[2].button("↻ Refresh", key="gcv_load"):
        _structures.clear()
        _history.clear()

    try:
        packed, notes = _structures(allow_live, settlement.isoformat())
    except Exception as exc:  # noqa: BLE001 - never let a live failure blank the tab
        packed, notes = _structures(False, settlement.isoformat())
        notes = list(notes) + [f"Live fetch failed, showing snapshots ({exc})"]
    curves = [_as_curve(d) for d in packed]

    sub = st.tabs(["📐 Term structures", "🕰️ Benchmark history",
                   "📊 Spreads & shape", "🐍 Code"])

    # ---------------------------------------------------------- term structures
    with sub[0]:
        if not curves:
            st.warning("No market has usable curve data yet.")
        else:
            st.caption(
                "Outliers are rejected with a MAD-based rule, which removes "
                "inflation-linked paper (it quotes a *real* yield), stale quotes "
                "and illiquid off-the-runs — all the same statistical problem.")
            chosen = st.multiselect(
                "Markets", [c.code for c in curves],
                default=[c.code for c in curves],
                format_func=lambda c: f"{gcv.MARKETS[c].name} ({gcv.MARKETS[c].instrument})")
            shown = [c for c in curves if c.code in chosen]

            if shown:
                grid = np.linspace(0.25, 30.0, 160)
                fitted = {}
                for c in shown:
                    if c.fit is None or c.points.empty:
                        continue
                    lo = float(c.points["years"].min())
                    hi = float(c.points["years"].max())
                    vals = c.fit.evaluate(grid)
                    vals = np.where((grid >= lo) & (grid <= hi), vals, np.nan)
                    fitted[f"{c.name} ({c.instrument})"] = vals
                if fitted:
                    frame = pd.DataFrame(fitted, index=grid)
                    frame.index.name = "Years to maturity"
                    st.plotly_chart(
                        ch.line(frame, title="Government curves — Nelson-Siegel fits",
                                height=420, yfmt=".2f"),
                        width="stretch")

                focus_code = st.selectbox(
                    "Show the instruments behind one fit",
                    [c.code for c in shown],
                    format_func=lambda c: gcv.MARKETS[c].name)
                focus = next(c for c in shown if c.code == focus_code)
                if focus.fit is not None and not focus.points.empty:
                    scatter_src = focus.points.assign(
                        Instrument=gcv.MARKETS[focus_code].instrument)
                    st.plotly_chart(
                        ch.scatter(scatter_src, x="years", y="yield",
                                   label="Instrument", group="Instrument",
                                   title=f"{focus.name} — quoted yields vs the fitted curve",
                                   xtitle="Years to maturity", ytitle="Yield",
                                   suffix="%", height=380),
                        width="stretch")
                    f = focus.fit
                    m = st.columns(5)
                    m[0].metric("Instruments quoted", f"{focus.n_instruments:,}")
                    m[1].metric("Used in fit", f"{f.n_points:,}")
                    m[2].metric("Rejected as outliers", f"{f.n_dropped:,}")
                    m[3].metric("Fit error (RMSE)", f"{f.rmse * 100:.1f} bps")
                    m[4].metric("Quotes as of", focus.as_of or "—")

                st.markdown("**Nelson-Siegel factors**")
                st.caption(
                    "β₀ is the level the curve flattens to; β₀+β₁ is the implied "
                    "instantaneous short rate; β₂ is the curvature (the hump) and "
                    "λ is where it sits.")
                rows = []
                for c in shown:
                    if c.fit is None:
                        continue
                    mt = gcv.curve_metrics(c)
                    rows.append({
                        "Market": c.name, "Instrument": c.instrument,
                        "β₀ level": round(c.fit.beta0, 3),
                        "β₁ slope": round(c.fit.beta1, 3),
                        "β₂ curvature": round(c.fit.beta2, 3),
                        "λ (yrs)": round(c.fit.lam, 2),
                        "Short rate %": round(c.fit.short_rate, 3),
                        "2y %": None if mt["2y"] is None else round(mt["2y"], 3),
                        "10y %": None if mt["10y"] is None else round(mt["10y"], 3),
                        "30y %": None if mt["30y"] is None else round(mt["30y"], 3),
                        "RMSE bps": round(c.fit.rmse * 100, 1),
                        "Source": c.source,
                    })
                if rows:
                    st.dataframe(pd.DataFrame(rows), width="stretch",
                                 hide_index=True)

        if notes:
            with st.expander(f"⚠️ {len(notes)} market(s) without instrument-level data"):
                for n in notes:
                    st.markdown(f"- {n}")
                st.caption(
                    "Export the same LSEG **Comparable Bonds** sheet for the "
                    "market, run `python scripts/build_lseg_dataset.py <file.xlsx>` "
                    "and commit the result — the market then appears here "
                    "automatically.")

    # -------------------------------------------------------- benchmark history
    with sub[1]:
        st.subheader("10-year benchmark yields through time")
        st.caption(
            "The same maturity, every major market, over decades — so the euro "
            "crisis, the ZIRP years and the 2022 rate shock are all on one axis.")
        picks = st.multiselect(
            "Markets", list(gcv.FRED_BENCHMARK_10Y.keys()),
            default=["IT", "DE", "FR", "ES", "GB", "US", "JP"],
            format_func=lambda c: gcv.MARKETS[c].name)
        hist, status = _history(tuple(picks), allow_live)

        if hist.empty:
            st.info("No benchmark history available.")
        else:
            if status.startswith("live:"):
                st.success(f"🟢 Live data — {status.split(':', 1)[1].upper()}, "
                           f"{len(hist)} observations to {hist.index.max().date()}.")
            else:
                st.warning(
                    "🟡 Committed snapshot (live fetch unavailable from this "
                    "host). On the deployed app the network is open and this "
                    "refreshes itself.")
            yr = st.slider("From year", int(hist.index.year.min()),
                           int(hist.index.year.max()),
                           max(int(hist.index.year.min()), 1995))
            view = hist[hist.index.year >= yr]
            st.plotly_chart(
                ch.line(view, title="10-year government bond yields (%)",
                        height=420, yfmt=".2f"),
                width="stretch")

            if view.shape[1] >= 2:
                st.markdown("**Correlation of monthly yield changes**")
                st.plotly_chart(
                    ch.heatmap(view.diff().dropna().corr(),
                               title="Yield-change correlation", height=360),
                    width="stretch")

            k = st.columns(min(len(view.columns), 5))
            for col, name in zip(k, view.columns[:5]):
                series = view[name].dropna()
                if not series.empty:
                    col.metric(name, f"{series.iloc[-1]:.2f}%",
                               delta=f"{series.iloc[-1] - series.iloc[0]:+.2f} pp "
                                     f"since {yr}")

    # ------------------------------------------------------------ spreads/shape
    with sub[2]:
        st.subheader("Spreads and curve shape")
        if len(curves) < 1:
            st.info("Load at least one curve first.")
        else:
            c1, c2 = st.columns(2)
            bench = c1.selectbox("Benchmark market", [c.code for c in curves],
                                 format_func=lambda c: gcv.MARKETS[c].name)
            tenor = c2.select_slider("Tenor (years)",
                                     [2.0, 5.0, 10.0, 20.0, 30.0], value=10.0)
            table = gcv.spread_table(curves, benchmark=bench, tenor=tenor)
            st.caption(
                f"Spread to the {gcv.MARKETS[bench].instrument} at {tenor:g} "
                "years, in basis points.")
            st.dataframe(table, width="stretch", hide_index=True)

            spread_col = f"Spread vs {bench} (bps)"
            plot_src = table.dropna(subset=[spread_col])
            if len(plot_src) > 1:
                st.plotly_chart(
                    ch.hbar(plot_src["Market"], plot_src[spread_col],
                            title=f"{tenor:g}y spread vs {gcv.MARKETS[bench].instrument}",
                            height=340, value_fmt="{:.0f}", suffix=" bps",
                            color_by_sign=True),
                    width="stretch")

            st.markdown("**Curve shape**")
            st.caption(
                "2s10s is the slope quoted as a cycle signal — a negative reading "
                "(inversion) has preceded most post-war US recessions.")
            shape = []
            for c in curves:
                mt = gcv.curve_metrics(c)
                shape.append({
                    "Market": c.name,
                    "2y %": None if mt["2y"] is None else round(mt["2y"], 3),
                    "10y %": None if mt["10y"] is None else round(mt["10y"], 3),
                    "30y %": None if mt["30y"] is None else round(mt["30y"], 3),
                    "2s10s bps": None if mt["2s10s"] is None else round(mt["2s10s"] * 100, 1),
                    "10s30s bps": None if mt["10s30s"] is None else round(mt["10s30s"] * 100, 1),
                    "Inverted": "yes" if mt["inverted"] else "no",
                })
            shape_df = pd.DataFrame(shape)
            st.dataframe(shape_df, width="stretch", hide_index=True)
            slopes = shape_df.dropna(subset=["2s10s bps"])
            if len(slopes) > 1:
                st.plotly_chart(
                    ch.hbar(slopes["Market"], slopes["2s10s bps"],
                            title="2s10s slope (bps) — negative = inverted",
                            height=320, value_fmt="{:.0f}", suffix=" bps",
                            color_by_sign=True),
                    width="stretch")

    # -------------------------------------------------------------------- code
    with sub[3]:
        st.subheader("Edit & run the curve engine")
        seed = (
            "# `gcv` (government-curve engine) plus pd / np are available — no\n"
            "# imports. load_term_structures returns (curves, notes); each curve\n"
            "# has .yield_at(years), .fit and .code. Set `result` to display it.\n\n"
            "curves, notes = gcv.load_term_structures(allow_live=False)\n"
            "result = {c.code: round(c.yield_at(10.0) or 0.0, 4) for c in curves}\n"
            "print(f'{len(curves)} curves, 10y yields:')\n"
        )

        def _ctx():
            return {"gcv": gcv, "pd": pd, "np": np}

        def _render_curve(result):
            if isinstance(result, dict) and result:
                ser = pd.Series(result, dtype=float).sort_values(ascending=False)
                st.plotly_chart(
                    ch.hbar(ser.index, ser.values * 100,
                            title="Result by market", height=320,
                            value_fmt="{:.2f}", suffix="%"),
                    width="stretch")
                st.dataframe(
                    pd.DataFrame({"market": ser.index, "value": ser.values}),
                    width="stretch", hide_index=True)
            else:
                st.write(result)

        code_editor(
            seed, _ctx, result_var="result", render_result=_render_curve,
            key="gov_curves", title="Live curve editor",
            intro="Runs the same Nelson-Siegel curve engine the page uses. Read a "
                  "fitted yield at any maturity, compare markets, inspect the fit.",
            filename="algohns_gov_curves_live.py")
