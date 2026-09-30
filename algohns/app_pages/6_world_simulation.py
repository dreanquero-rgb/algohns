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
from algohns.modules import news_engine as ne
from algohns.modules import stochastic as sto
from algohns.ui import code_panel, header

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


tab_globe, tab_mc, tab_dist, tab_cmp, tab_cat, tab_code = st.tabs(
    ["Interactive globe", "Monte Carlo engine", "Outcome distribution",
     "Portfolio comparison", "Event catalogue", "Code"]
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

# =============================================================================
# TAB 2 — MONTE CARLO ENGINE
#
# The forward test's quantitative core, exposed directly: pick a price-formation
# process, see what it implies for the distribution of outcomes, and see how
# news re-parameterises it. Every process is validated against its closed-form
# moments in tests/test_stochastic.py.
# =============================================================================
with tab_mc:
    st.subheader("Price formation — stochastic processes and Monte Carlo")
    st.caption(
        "A forward test is a distribution, not a path. Pick the process that "
        "generates returns, draw many paths, and read the tails. Each process "
        "holds E[S_T] = S₀·e^(μT) whatever its volatility, tail or jump "
        "parameters, so changing the risk never silently changes the expected "
        "return — which is what makes two settings comparable."
    )

    m1, m2, m3, m4 = st.columns(4)
    process = m1.selectbox("Process", list(sto.PROCESSES.keys()),
                           format_func=lambda k: sto.PROCESSES[k])
    mu_pct = m2.slider("Drift μ (%/yr)", -5.0, 20.0, 7.0, 0.5)
    sigma_pct = m3.slider("Volatility σ (%/yr)", 5.0, 80.0, 20.0, 1.0)
    years_mc = m4.slider("Horizon (years)", 1, 10, 5, key="mc_years")

    p1, p2, p3, p4 = st.columns(4)
    n_paths = p1.select_slider("Paths", [500, 1_000, 2_000, 5_000, 10_000], value=2_000)
    jump_lambda = p2.slider("Jump intensity λ (/yr)", 0.0, 20.0, 4.0, 0.5,
                            help="Merton only: expected news-driven jumps per year.")
    df_t = p3.slider("Student-t df", 2.5, 30.0, 4.0, 0.5,
                     help="Student-t only: lower = fatter tails. df→∞ is Gaussian.")
    mc_seed = p4.number_input("Seed", 1, 99_999, 2026, 1, key="mc_seed")

    @st.cache_data(ttl=900, show_spinner="Drawing Monte Carlo paths…")
    def _mc(process: str, mu: float, sigma: float, years: int, paths: int,
            lam: float, df: float, seed: int):
        params = sto.ProcessParams(
            mu=mu, sigma=sigma, steps_per_year=252, df=df,
            jump_intensity=lam, jump_mean=-0.03, jump_vol=0.10,
            kappa=2.0, theta=sigma**2, xi=0.30, rho=-0.7,
        )
        rng = np.random.default_rng(seed)
        arr = sto.simulate_paths(process, 100.0, params, paths, 252 * years, rng)
        return arr, sto.terminal_statistics(arr)

    paths_arr, stats = _mc(process, mu_pct / 100, sigma_pct / 100, years_mc,
                           n_paths, jump_lambda, df_t, int(mc_seed))

    k = st.columns(5)
    k[0].metric("Median return", f"{stats['median_return']:.1%}")
    k[1].metric("Mean return", f"{stats['mean_return']:.1%}")
    k[2].metric("VaR 95%", f"{stats['var_95']:.1%}",
                help="5th percentile of the terminal return.")
    k[3].metric("Expected shortfall 95%", f"{stats['expected_shortfall_95']:.1%}",
                help="Average return in the worst 5% of paths.")
    k[4].metric("Probability of loss", f"{stats['prob_loss']:.0%}")

    # --- Fan chart: the distribution through time, not one path -------------
    pcts = [5, 25, 50, 75, 95]
    step_idx = np.linspace(0, paths_arr.shape[1] - 1, min(260, paths_arr.shape[1])).astype(int)
    fan = pd.DataFrame(
        {f"p{q}": np.percentile(paths_arr[:, step_idx], q, axis=0) for q in pcts},
        index=(step_idx / 252.0),
    )
    fan.index.name = "Years"
    st.plotly_chart(
        ch.line(fan, title=f"Monte Carlo fan — {sto.PROCESSES[process]}", height=380),
        width="stretch",
    )
    st.caption(
        f"{n_paths:,} paths. The p5–p95 band is the forward test's actual claim; "
        "the median line is not a forecast. Mean above median is the lognormal "
        "asymmetry — quoting the mean as 'expected outcome' is the classic error."
    )

    # --- Terminal distribution ---------------------------------------------
    d1, d2 = st.columns(2)
    with d1:
        terminal_ret = paths_arr[:, -1] / paths_arr[:, 0] - 1.0
        counts, edges = np.histogram(terminal_ret, bins=40)
        centres = [f"{(edges[i] + edges[i + 1]) / 2:.0%}" for i in range(len(counts))]
        st.plotly_chart(
            ch.bar(centres, counts, title="Terminal return distribution",
                   height=340),
            width="stretch")
    with d2:
        risk = pd.DataFrame({
            "Value": {
                "Median return": stats["median_return"],
                "Mean return": stats["mean_return"],
                "Std of return": stats["std_return"],
                "VaR 95%": stats["var_95"],
                "Expected shortfall 95%": stats["expected_shortfall_95"],
                "Worst path": stats["worst_return"],
                "Best path": stats["best_return"],
                "Deepest drawdown": stats["max_drawdown"],
            }
        })
        st.dataframe(risk.style.format("{:.2%}"), width="stretch", height=340)

    # --- Process comparison on identical parameters ------------------------
    st.divider()
    st.markdown("**Why the process choice matters**")
    st.caption(
        "Same drift, same volatility, same seed — only the law of the increments "
        "changes. The means agree by construction; the tails do not. That gap is "
        "the risk a pure-GBM forward test silently omits."
    )

    @st.cache_data(ttl=900, show_spinner="Comparing processes…")
    def _compare(mu: float, sigma: float, years: int, paths: int, lam: float,
                 df: float, seed: int) -> pd.DataFrame:
        rows = {}
        for name in sto.PROCESSES:
            params = sto.ProcessParams(
                mu=mu, sigma=sigma, steps_per_year=252, df=df,
                jump_intensity=lam, jump_mean=-0.03, jump_vol=0.10,
                kappa=2.0, theta=sigma**2, xi=0.30, rho=-0.7)
            arr = sto.simulate_paths(name, 100.0, params, paths,
                                     252 * years, np.random.default_rng(seed))
            s = sto.terminal_statistics(arr)
            rows[name] = {
                "Mean %": s["mean_return"] * 100,
                "Median %": s["median_return"] * 100,
                "VaR 95 %": s["var_95"] * 100,
                "ES 95 %": s["expected_shortfall_95"] * 100,
                "Worst %": s["worst_return"] * 100,
                "Max DD %": s["max_drawdown"] * 100,
                "P(loss) %": s["prob_loss"] * 100,
            }
        return pd.DataFrame(rows).T

    cmp_df = _compare(mu_pct / 100, sigma_pct / 100, years_mc,
                      min(n_paths, 3_000), jump_lambda, df_t, int(mc_seed))
    cc1, cc2 = st.columns([3, 2])
    with cc1:
        st.plotly_chart(
            ch.grouped_bar(cmp_df[["VaR 95 %", "ES 95 %", "Max DD %"]],
                           title="Downside risk by process", height=340),
            width="stretch")
    with cc2:
        st.dataframe(cmp_df.style.format("{:.1f}"), width="stretch", height=340)

    # --- News modulation ----------------------------------------------------
    st.divider()
    st.markdown("**News does not paint the path — it re-parameterises it**")
    st.caption(
        "This is the mechanism behind the forward test. A news item shifts the "
        "drift, scales the volatility and adds jump intensity while it is "
        "active; the price keeps obeying its SDE inside that regime. Below, the "
        "same seed is drawn with and without a news regime running."
    )
    steps_mc = 252 * years_mc
    news_subjects = {
        "company": sorted(c.ticker for c in COMPANIES),
        "sector": sorted({c.sector for c in COMPANIES}),
        "country": sorted({c.domicile for c in COMPANIES}),
        "region": ["Europe", "Asia", "Americas", "EMEA"],
        "chokepoint": ["Suez Canal", "Strait of Hormuz", "Taiwan Strait",
                       "Panama Canal", "Strait of Malacca", "Red Sea"],
    }
    drawn = ne.sample_news(365 * years_mc, news_subjects, seed=int(mc_seed))
    vol_mult = np.ones(steps_mc)
    drift_shift = np.zeros(steps_mc)
    lam_shift = np.zeros(steps_mc)
    for item in drawn:
        lo = int(item.day / 365.0 * 252)
        hi = min(int(item.end_day / 365.0 * 252), steps_mc)
        if hi > lo:
            vol_mult[lo:hi] = np.maximum(vol_mult[lo:hi], item.vol_multiplier)
            drift_shift[lo:hi] += item.drift_change
            lam_shift[lo:hi] += abs(item.equity_shock) * 8.0
    modulation = sto.NewsModulation(drift_shift=drift_shift,
                                    vol_multiplier=vol_mult,
                                    intensity_shift=lam_shift)
    mc_params = sto.ProcessParams(mu=mu_pct / 100, sigma=sigma_pct / 100,
                                  steps_per_year=252, df=df_t,
                                  jump_intensity=jump_lambda, jump_mean=-0.03,
                                  jump_vol=0.10, theta=(sigma_pct / 100) ** 2)
    quiet = sto.simulate_paths(process, 100.0, mc_params, 600, steps_mc,
                               np.random.default_rng(int(mc_seed)))
    newsy = sto.simulate_paths(process, 100.0, mc_params, 600, steps_mc,
                               np.random.default_rng(int(mc_seed)), modulation)

    n1, n2 = st.columns(2)
    with n1:
        regime = pd.DataFrame({"Volatility multiplier": vol_mult},
                              index=np.arange(steps_mc) / 252.0)
        regime.index.name = "Years"
        st.plotly_chart(
            ch.line(regime, title="News-driven volatility regime", height=320),
            width="stretch")
    with n2:
        band = pd.DataFrame({
            "No news p5": np.percentile(quiet[:, step_idx], 5, axis=0),
            "No news p95": np.percentile(quiet[:, step_idx], 95, axis=0),
            "With news p5": np.percentile(newsy[:, step_idx], 5, axis=0),
            "With news p95": np.percentile(newsy[:, step_idx], 95, axis=0),
        }, index=step_idx / 252.0)
        band.index.name = "Years"
        st.plotly_chart(
            ch.line(band, title="Outcome band — news widens the distribution",
                    height=320),
            width="stretch")

    qs, ns = sto.terminal_statistics(quiet), sto.terminal_statistics(newsy)
    e = st.columns(4)
    e[0].metric("VaR 95 — no news", f"{qs['var_95']:.1%}")
    e[1].metric("VaR 95 — with news", f"{ns['var_95']:.1%}",
                delta=f"{(ns['var_95'] - qs['var_95']) * 100:.1f} pp")
    e[2].metric("News items drawn", f"{len(drawn):,}")
    e[3].metric("Peak vol multiplier", f"{vol_mult.max():.2f}×")

    # --- Stochastic short rate ---------------------------------------------
    st.divider()
    st.markdown("**The risk-free rate is stochastic too**")
    st.caption(
        "Discounting against a constant rate is the other quiet assumption in a "
        "naive forward test. Vasicek is mean-reverting and Gaussian (it can go "
        "negative — which post-2014 Europe requires); CIR mean-reverts with a "
        "√r diffusion that keeps it non-negative."
    )
    r1c, r2c, r3c = st.columns(3)
    r0 = r1c.slider("Starting short rate (%)", -1.0, 10.0, 2.5, 0.1) / 100
    kappa_r = r2c.slider("Mean-reversion speed κ", 0.1, 5.0, 1.2, 0.1)
    theta_r = r3c.slider("Long-run level θ (%)", -1.0, 8.0, 2.0, 0.1) / 100
    rate_steps = 252 * years_mc
    vas = sto.vasicek_paths(r0, kappa_r, theta_r, 0.010, 600, rate_steps, 252,
                            np.random.default_rng(int(mc_seed)))
    cir = sto.cir_paths(max(r0, 0.0001), kappa_r, max(theta_r, 0.0001), 0.06,
                        600, rate_steps, 252, np.random.default_rng(int(mc_seed)))
    rate_df = pd.DataFrame({
        "Vasicek p50": np.percentile(vas[:, step_idx], 50, axis=0) * 100,
        "Vasicek p5": np.percentile(vas[:, step_idx], 5, axis=0) * 100,
        "Vasicek p95": np.percentile(vas[:, step_idx], 95, axis=0) * 100,
        "CIR p50": np.percentile(cir[:, step_idx], 50, axis=0) * 100,
        "CIR p5": np.percentile(cir[:, step_idx], 5, axis=0) * 100,
    }, index=step_idx / 252.0)
    rate_df.index.name = "Years"
    st.plotly_chart(ch.line(rate_df, title="Short-rate paths (%)", height=340),
                    width="stretch")
    rr = st.columns(3)
    rr[0].metric("Vasicek terminal mean", f"{vas[:, -1].mean() * 100:.2f}%")
    rr[1].metric("Negative-rate paths (Vasicek)", f"{(vas[:, -1] < 0).mean():.0%}")
    rr[2].metric("Feller satisfied (CIR)",
                 "yes" if sto.feller_condition(kappa_r, max(theta_r, 0.0001), 0.06) else "no",
                 help="2κθ ≥ σ² means CIR cannot reach zero.")


# =============================================================================
# TAB 5 — CODE
# =============================================================================
with tab_code:
    st.subheader("Simulation engine source")
    st.caption(
        "The forward test's two new engines, pulled live with `inspect`. The "
        "stochastic module carries the SDEs and their discretisations; the news "
        "engine carries the combinatorial event space and how an item maps to a "
        "quantitative shock."
    )
    cat = ne.catalogue_stats({
        "company": len(COMPANIES),
        "sector": len({c.sector for c in COMPANIES}),
        "country": len({c.domicile for c in COMPANIES}),
        "region": 4, "chokepoint": 6,
    })
    g = st.columns(4)
    g[0].metric("Event frames", f"{cat['frames']:,}")
    g[1].metric("News kinds", f"{cat['kinds']:,}")
    g[2].metric("Distinct headlines", f"{cat['distinct_headlines']:,}")
    g[3].metric("Favourable share", f"{cat['favourable_share']:.0%}")
    st.caption(
        f"The event space is the product frame × action × driver × magnitude × "
        f"subject, so {cat['frames']} frames expand to {cat['kinds']:,} news kinds "
        f"and {cat['distinct_headlines']:,} distinct headlines — against ~49 in the "
        "original hand-written list."
    )
    code_panel(
        [("Stochastic processes", sto), ("News engine", ne)],
        title="Forward-simulation engine — full source",
        intro="GBM, Student-t, Merton jump-diffusion, Heston, Vasicek and CIR, "
              "plus the news space and its coupling to the price processes.",
        expanded=True, filename="algohns_forward_engine.py",
    )
