"""Streamlit page — Global government bond curves.

BTP vs Bund vs OAT vs Bonos vs Gilt vs Treasury vs JGB, on one axis, fitted
rather than interpolated.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import streamlit as st

from algohns import charts as ch
from algohns.modules import gov_curves as gcv
from algohns.ui import code_panel, header

header(
    "Global Government Bond Curves",
    "BTP · Bund · OAT · Bonos · Gilt · Treasury · JGB — term structures, "
    "spreads and Nelson-Siegel factors.",
    badge="Module 5",
)

tab_curves, tab_history, tab_spreads, tab_code = st.tabs(
    ["📐 Term structures", "🕰️ Benchmark history", "📊 Spreads & shape", "🐍 Code"]
)


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


allow_live = st.sidebar.toggle(
    "Fetch live data", value=True,
    help="Live multi-country yields come from FRED (keyless, no rate cap). "
         "Where the network is blocked the page falls back to committed real "
         "snapshots and says so.")
settlement = st.sidebar.date_input("Settlement", value=date.today())

packed, notes = _structures(allow_live, settlement.isoformat())
curves = [_as_curve(d) for d in packed]

# =============================================================================
# TAB 1 — TERM STRUCTURES
# =============================================================================
with tab_curves:
    if not curves:
        st.warning("No market has usable curve data yet.")
    else:
        st.subheader("Fitted term structures")
        st.caption(
            "Each curve is a **Nelson-Siegel fit** to real instrument quotes, not "
            "a line through the nearest two bonds. Outliers are rejected with a "
            "MAD-based rule, which is what removes inflation-linked paper (it "
            "quotes a *real* yield), stale quotes and illiquid off-the-runs — all "
            "the same statistical problem."
        )

        chosen = st.multiselect(
            "Markets", [c.code for c in curves],
            default=[c.code for c in curves],
            format_func=lambda c: f"{gcv.MARKETS[c].name} ({gcv.MARKETS[c].instrument})",
        )
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
                # Do not draw the curve where no instrument supports it.
                vals = np.where((grid >= lo) & (grid <= hi), vals, np.nan)
                fitted[f"{c.name} ({c.instrument})"] = vals
            if fitted:
                frame = pd.DataFrame(fitted, index=grid)
                frame.index.name = "Years to maturity"
                st.plotly_chart(
                    ch.line(frame, title="Government curves — Nelson-Siegel fits",
                            height=420, yfmt=".2f"),
                    width="stretch")

            # Actual instruments behind the fit, for one market at a time.
            focus_code = st.selectbox(
                "Show the instruments behind one fit",
                [c.code for c in shown],
                format_func=lambda c: gcv.MARKETS[c].name)
            focus = next(c for c in shown if c.code == focus_code)
            if focus.fit is not None and not focus.points.empty:
                scatter_src = focus.points.assign(Instrument=gcv.MARKETS[focus_code].instrument)
                st.plotly_chart(
                    ch.scatter(scatter_src, x="years", y="yield", label="Instrument",
                               group="Instrument",
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
                "instantaneous short rate; β₂ is the curvature (the hump) and λ "
                "is where it sits. These four numbers summarise an entire curve — "
                "which is why they are what rates desks actually compare."
            )
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
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    if notes:
        with st.expander(f"⚠️ {len(notes)} market(s) without instrument-level data"):
            for n in notes:
                st.markdown(f"- {n}")
            st.caption(
                "Export the same LSEG **Comparable Bonds** sheet for the market "
                "(one file per country), run "
                "`python scripts/build_lseg_dataset.py <file.xlsx>` and commit the "
                "result — the market then appears here automatically. The registry "
                "in `gov_curves.MARKETS` already knows the filenames it expects, "
                "so adding a country is a data drop, not a code change."
            )

# =============================================================================
# TAB 2 — BENCHMARK HISTORY
# =============================================================================
with tab_history:
    st.subheader("10-year benchmark yields through time")
    st.caption(
        "The cross-country comparison a securities course actually wants: the "
        "same maturity, every major market, over decades — so the euro crisis, "
        "the ZIRP years and the 2022 rate shock are all visible on one axis."
    )
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
                "🟡 Committed snapshot (live fetch unavailable from this host). "
                "On the deployed app the network is open and this refreshes itself."
            )
        yr = st.slider("From year", int(hist.index.year.min()),
                       int(hist.index.year.max()), max(int(hist.index.year.min()), 1995))
        view = hist[hist.index.year >= yr]
        st.plotly_chart(
            ch.line(view, title="10-year government bond yields (%)", height=420,
                    yfmt=".2f"),
            width="stretch")

        if view.shape[1] >= 2:
            st.markdown("**Correlation of monthly yield changes**")
            st.caption(
                "How much these markets move together. Euro-area sovereigns are "
                "tightly coupled; Japan is the outlier — which is the point of "
                "holding it."
            )
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

# =============================================================================
# TAB 3 — SPREADS & SHAPE
# =============================================================================
with tab_spreads:
    st.subheader("Spreads and curve shape")
    if len(curves) < 1:
        st.info("Load at least one curve first.")
    else:
        c1, c2 = st.columns(2)
        bench = c1.selectbox("Benchmark market", [c.code for c in curves],
                             format_func=lambda c: gcv.MARKETS[c].name)
        tenor = c2.select_slider("Tenor (years)", [2.0, 5.0, 10.0, 20.0, 30.0],
                                 value=10.0)
        table = gcv.spread_table(curves, benchmark=bench, tenor=tenor)
        st.caption(
            f"Spread to the {gcv.MARKETS[bench].instrument} at {tenor:g} years, in "
            "basis points — how euro-area sovereign risk is actually quoted."
        )
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
            "2s10s is the slope every rates desk quotes as a cycle signal — a "
            "negative reading (inversion) has preceded most post-war US "
            "recessions. 10s30s says whether the long end is pricing term "
            "premium or just expectations."
        )
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

# =============================================================================
# TAB 4 — CODE
# =============================================================================
with tab_code:
    st.subheader("Curve engine source")
    st.caption(
        "The data resolution order, the Nelson-Siegel fit and the robust outlier "
        "rejection, pulled live with `inspect`."
    )
    st.markdown(
        "**Where the numbers come from, in order.** "
        "1 · *LSEG instrument-level exports* committed in the repo — real ISINs "
        "and bid yields, the highest-quality input. "
        "2 · *FRED* for live multi-country 10-year benchmarks and the full US "
        "curve: keyless and without a rate cap a dashboard would hit. "
        "3 · *Committed snapshots*, so the page still works with no network.\n\n"
        "The MCP connectors were checked first and are **not** the live path: "
        "Alpha Vantage covers the US curve but its free tier allows 25 requests "
        "a day, and Bigdata.com's country tearsheets state that treasury yields "
        "are US-only. Neither can produce a euro-area or gilt curve, so they are "
        "used to pull development snapshots while FRED serves production."
    )
    code_panel(
        [("Nelson-Siegel fit", gcv.fit_nelson_siegel),
         ("Instrument-level curve", gcv.lseg_term_structure),
         ("Live FRED path", gcv.fred_benchmark_history),
         ("Full module", gcv)],
        title="Government curve engine — full source",
        intro="Curve fitting, robust outlier rejection and source resolution.",
        expanded=True, filename="algohns_gov_curves.py",
    )
