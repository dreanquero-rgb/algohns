"""Streamlit page — Module 6: forward-looking World Simulation.

Two surfaces, split along what each tool is good at:

* the **interactive globe** is a self-contained HTML component, because
  Streamlit re-runs its script on every widget change and hosts components
  in an iframe — a per-tick animated globe driven from Python would stutter;
* the **Python analysis** below is where Streamlit earns its keep: many
  seeds, distributions, and two portfolios compared on one identical world.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from algohns import charts as ch
from algohns.modules.world_events import EVENT_CATALOGUE, expected_event_market_drift
from algohns.modules.world_export import DEFAULT_PORTFOLIO
from algohns.modules.world_forward import ForwardConfig, build_world, simulate_world
from algohns.modules.world_universe import COMPANIES
from algohns.ui import header

header(
    "World Simulation",
    "Forward-looking test: the world evolves, companies grow and fail, "
    "and the portfolio is valued along the path.",
    badge="Module 6",
)

st.warning(
    "**Not validated on historical episodes.** It is meant for comparing "
    "portfolios on the *same* simulated world, not as a forecast. The dataset "
    "is hand-curated: the dependency percentages are model assumptions, not "
    "reported data."
)

GLOBE = Path(__file__).resolve().parent.parent.parent / "public" / "world" / "index.html"


@st.cache_resource(show_spinner=False)
def _graph():
    return build_world()


@st.cache_data(ttl=1800, show_spinner="Simulating worlds…")
def _many(years: int, intensity: float, n_seeds: int, weights_key: tuple) -> pd.DataFrame:
    graph = _graph()
    weights = dict(weights_key)
    rows = []
    for seed in range(n_seeds):
        tl = simulate_world(
            graph,
            ForwardConfig(horizon_days=365 * years, seed=seed,
                          event_intensity=intensity),
        )
        arr = np.array(tl.portfolio_path(weights))
        eq = 1.0 + arr
        dd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
        rows.append({
            "seed": seed,
            "Portfolio %": arr[-1] * 100,
            "Market %": (tl.market_index[-1] / tl.market_index[0] - 1) * 100,
            "Max DD %": dd * 100,
            "Failures": len(tl.bankruptcies),
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=1800, show_spinner="Simulating the world…")
def _single(years: int, seed: int, intensity: float, a_key: tuple, b_key: tuple):
    tl = simulate_world(
        _graph(),
        ForwardConfig(horizon_days=365 * years, seed=seed,
                      event_intensity=intensity),
    )
    return (
        tl.calendar_days,
        tl.portfolio_path(dict(a_key)),
        tl.portfolio_path(dict(b_key)),
        list(tl.market_index),
        dict(tl.bankruptcies),
        tl.news_for(dict(a_key)),
        list(tl.warnings),
    )


tab_globe, tab_dist, tab_cmp, tab_cat = st.tabs(
    ["Interactive globe", "Outcome distribution", "Portfolio comparison", "Event catalogue"]
)

with tab_globe:
    if GLOBE.exists():
        st.caption(
            "Drag to rotate, double-click to stop the rotation. The portfolio "
            "can be edited **during** the simulation: the world does not depend "
            "on what you hold, so re-valuation is instant."
        )
        st.iframe(GLOBE.read_text(), height=920)
    else:
        st.error(
            f"Globe not found at `{GLOBE}`. Regenerate it with "
            "`python scripts/build_world.py`."
        )

with tab_dist:
    st.subheader("Outcomes across many worlds")
    st.caption(
        "A single world is an anecdote. The distribution across many seeds is "
        "the only reading that says anything about the portfolio."
    )
    c1, c2, c3 = st.columns(3)
    years = c1.slider("Horizon (years)", 1, 10, 5)
    intensity = c2.slider("Turbulence", 0.2, 3.0, 1.0, 0.1)
    n_seeds = c3.slider("Number of worlds", 10, 100, 30, 10)

    frame = _many(years, intensity, n_seeds,
                  tuple(sorted(DEFAULT_PORTFOLIO.items())))

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Portfolio (median)", f"{frame['Portfolio %'].median():.1f}%")
    k2.metric("Market (median)", f"{frame['Market %'].median():.1f}%")
    k3.metric("Max DD (median)", f"{frame['Max DD %'].median():.1f}%")
    k4.metric("Worlds at a loss", f"{(frame['Portfolio %'] < 0).mean():.0%}")

    st.plotly_chart(
        ch.grouped_bar(
            frame.set_index("seed")[["Portfolio %", "Market %"]],
            title="Outcome per simulated world", height=340,
        ),
        width="stretch",
    )
    st.dataframe(
        frame[["Portfolio %", "Market %", "Max DD %", "Failures"]]
        .describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9])
        .style.format("{:.2f}"),
        width="stretch",
    )
    geometric = 0.07 - 0.16**2 / 2
    st.caption(
        f"Reference: the median of a GBM follows the **geometric** drift "
        f"({geometric:.2%}/yr = {((1 + geometric) ** years - 1):.1%} over "
        f"{years} years), not the arithmetic one. Comparing against the "
        "compounded arithmetic drift makes a correct calibration look broken."
    )

with tab_cmp:
    st.subheader("Two portfolios, the same world")
    st.caption(
        "The world is independent of the portfolio, so both run on the same "
        "path: the comparison isolates the portfolio, not luck."
    )
    tickers = sorted(c.ticker for c in COMPANIES)
    ca, cb = st.columns(2)
    a_names = ca.multiselect("Portfolio A", tickers,
                             default=list(DEFAULT_PORTFOLIO)[:8], key="w6a")
    b_names = cb.multiselect("Portfolio B", tickers,
                             default=["JNJ", "KO", "PG", "NEE", "WMT", "UNH"],
                             key="w6b")
    d1, d2, d3 = st.columns(3)
    seed = d1.number_input("World seed", 1, 99999, 2026, step=1)
    yrs = d2.slider("Horizon (years)", 1, 10, 5, key="w6y")
    inten = d3.slider("Turbulence", 0.2, 3.0, 1.0, 0.1, key="w6i")

    if not a_names or not b_names:
        st.info("Select at least one instrument for each portfolio.")
    else:
        days, pa, pb, mkt, fails, news, warns = _single(
            yrs, int(seed), inten,
            tuple((t, 1 / len(a_names)) for t in a_names),
            tuple((t, 1 / len(b_names)) for t in b_names),
        )
        curve = pd.DataFrame({
            "Portfolio A": np.array(pa) * 100,
            "Portfolio B": np.array(pb) * 100,
            "Market": (np.array(mkt) / mkt[0] - 1) * 100,
        }, index=days)
        curve.index.name = "Days"
        st.plotly_chart(
            ch.line(curve, title="Path on the same world", height=380,
                    yfmt=".1f"),
            width="stretch",
        )
        m1, m2, m3 = st.columns(3)
        m1.metric("A final", f"{pa[-1] * 100:.1f}%")
        m2.metric("B final", f"{pb[-1] * 100:.1f}%")
        m3.metric("Difference", f"{(pa[-1] - pb[-1]) * 100:+.1f} pp")

        if fails:
            st.error(
                "Failures in this world: "
                + ", ".join(f"{k} (d{v})" for k, v in sorted(fails.items(),
                                                             key=lambda kv: kv[1]))
            )
        for w in warns:
            st.caption(f"⚠️ {w}")

        st.markdown("**World newsfeed** — everything, not just the portfolio.")
        st.dataframe(
            pd.DataFrame([{
                "Day": n["day"], "Category": n["category"],
                "Relevance": n["portfolioRelevance"], "Severity": n["severity"],
                "Headline": n["headline"],
            } for n in news[:250]]),
            width="stretch", height=340,
        )

with tab_cat:
    st.subheader("Event catalogue")
    st.caption(
        "Intensities as annual rates calibrated on approximate historical base "
        "rates: order-of-magnitude judgements, not estimated parameters. They "
        "are the first thing to revisit once the historical-validation harness "
        "exists."
    )
    drag = expected_event_market_drift()
    fav = sum(1 for t in EVENT_CATALOGUE if t.favourable)
    q1, q2, q3 = st.columns(3)
    q1.metric("Templates", len(EVENT_CATALOGUE))
    q2.metric("Favourable", f"{fav / len(EVENT_CATALOGUE):.0%}",
              help="A generator of nothing but disasters produces a world in "
                   "which every portfolio loses: a test you cannot win teaches "
                   "nothing.")
    q3.metric("Net contribution to the market", f"{drag:.2%}/yr",
              help="Offset by the simulator, so the declared drift remains the "
                   "unconditional expectation.")
    st.dataframe(
        pd.DataFrame([{
            "Key": t.key, "Category": t.category.value,
            "Scope": t.scope.value, "Rate/yr": t.annual_rate,
            "Favourable": "yes" if t.favourable else "",
            "Severity": t.severity_label,
            "Impairment": f"{t.impairment[0]:.0%}–{t.impairment[1]:.0%}",
            "Equity shock": f"{t.equity_shock[0]:+.0%}–{t.equity_shock[1]:+.0%}",
            "Market shock": f"{t.market_shock[0]:+.0%}–{t.market_shock[1]:+.0%}",
            "Duration (days)": f"{t.duration_days[0]}–{t.duration_days[1]}",
        } for t in sorted(EVENT_CATALOGUE,
                          key=lambda x: (x.category.value, -x.annual_rate))]),
        width="stretch", height=520,
    )
