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

@st.cache_data(ttl=3600, show_spinner=False)
def _globe_html() -> str:
    """Read the self-contained globe once per session, not once per rerun."""
    return GLOBE.read_text()


with tab_globe:
    if GLOBE.exists():
        st.caption(
            "Drag to rotate, double-click to stop the rotation. The portfolio "
            "can be edited **during** the simulation: the world does not depend "
            "on what you hold, so re-valuation is instant."
        )
        st.iframe(_globe_html(), height=920)
    else:
        st.error(
            f"Globe not found at `{GLOBE}`. Regenerate it with "
            "`python scripts/build_world.py`."
        )

@st.fragment
def outcome_distribution_panel() -> None:
    st.subheader("Outcomes across many worlds")
    st.caption(
        "A single world is an anecdote. The distribution across many seeds is "
        "the only reading that says anything about the portfolio."
    )
    c1, c2, c3 = st.columns(3)
    years = c1.slider("Horizon (years)", 1, 10, 5)
    intensity = c2.slider("Turbulence", 0.2, 3.0, 1.0, 0.1)
    n_seeds = c3.slider("Number of worlds", 10, 100, 20, 10)

    # Gated on an explicit press for the same reason as the Monte Carlo tab:
    # each world is a full simulation (~0.3s), so 20 of them is a few seconds
    # and should never fire just because the page happened to re-run.
    if st.button("▶ Run the worlds", type="primary", key="run_dist",
                 help="Each world is a full forward simulation; 20 takes a few "
                      "seconds."):
        st.session_state["dist_args"] = (years, intensity, n_seeds)

    dist_args = st.session_state.get("dist_args")
    if not dist_args:
        st.info("Choose a horizon, turbulence and world count, then press "
                "**Run the worlds**.")
        return
    years, intensity, n_seeds = dist_args
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

@st.fragment
def portfolio_comparison_panel() -> None:
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

with tab_dist:
    outcome_distribution_panel()

with tab_cmp:
    portfolio_comparison_panel()

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
# The forward test's quantitative core. Wrapped in `st.fragment` and gated on an
# explicit Run: Streamlit re-runs a whole script on every widget change and does
# NOT lazily evaluate `st.tabs` bodies, so without both of these a slider
# anywhere on this page would re-execute a multi-second simulation even while
# you are looking at the globe. The fragment confines reruns to this panel; the
# button confines the compute to when it was actually asked for.
# =============================================================================
def _params(mu: float, sigma: float, df: float, lam: float) -> "sto.ProcessParams":
    return sto.ProcessParams(
        mu=mu, sigma=sigma, steps_per_year=252, df=df, jump_intensity=lam,
        jump_mean=-0.03, jump_vol=0.10, kappa=2.0, theta=sigma**2, xi=0.30,
        rho=-0.7)


@st.cache_data(ttl=900, show_spinner="Drawing Monte Carlo paths…")
def _mc(process: str, mu: float, sigma: float, years: int, paths: int,
        lam: float, df: float, seed: int):
    """Chunked Monte Carlo: peak memory stays ~50-90 MB even at 10k paths x 10y,
    where materialising every path peaked at ~800 MB and OOM-killed a 1 GB
    container. Percentiles remain exact."""
    return sto.simulate_summary(
        process, 100.0, _params(mu, sigma, df, lam), paths, 252 * years,
        np.random.default_rng(seed))


@st.cache_data(ttl=900, show_spinner="Comparing processes…")
def _compare(mu: float, sigma: float, years: int, paths: int, lam: float,
             df: float, seed: int) -> pd.DataFrame:
    rows = {}
    for name in sto.PROCESSES:
        s = sto.simulate_summary(name, 100.0, _params(mu, sigma, df, lam), paths,
                                 252 * years, np.random.default_rng(seed))["stats"]
        rows[name] = {
            "Mean %": s["mean_return"] * 100, "Median %": s["median_return"] * 100,
            "VaR 95 %": s["var_95"] * 100, "ES 95 %": s["expected_shortfall_95"] * 100,
            "Worst %": s["worst_return"] * 100, "Max DD %": s["max_drawdown"] * 100,
            "P(loss) %": s["prob_loss"] * 100,
        }
    return pd.DataFrame(rows).T


@st.cache_data(ttl=900, show_spinner="Sampling the news timeline…")
def _news_regime(years: int, seed: int, steps: int):
    """Turn a drawn news timeline into per-step process modulation."""
    subjects = {
        "company": sorted(c.ticker for c in COMPANIES),
        "sector": sorted({c.sector for c in COMPANIES}),
        "country": sorted({c.domicile for c in COMPANIES}),
        "region": ["Europe", "Asia", "Americas", "EMEA"],
        "chokepoint": ["Suez Canal", "Strait of Hormuz", "Taiwan Strait",
                       "Panama Canal", "Strait of Malacca", "Red Sea"],
    }
    drawn = ne.sample_news(365 * years, subjects, seed=seed)
    vol = np.ones(steps)
    drift = np.zeros(steps)
    lam = np.zeros(steps)
    for item in drawn:
        lo = int(item.day / 365.0 * 252)
        hi = min(int(item.end_day / 365.0 * 252), steps)
        if hi > lo:
            vol[lo:hi] = np.maximum(vol[lo:hi], item.vol_multiplier)
            drift[lo:hi] += item.drift_change
            lam[lo:hi] += abs(item.equity_shock) * 8.0
    return len(drawn), vol, drift, lam


@st.cache_data(ttl=900, show_spinner="Comparing with and without news…")
def _news_effect(process: str, mu: float, sigma: float, years: int, lam_j: float,
                 df: float, seed: int):
    steps = 252 * years
    n_items, vol, drift, lam = _news_regime(years, seed, steps)
    params = _params(mu, sigma, df, lam_j)
    modulation = sto.NewsModulation(drift_shift=drift, vol_multiplier=vol,
                                   intensity_shift=lam)
    quiet = sto.simulate_summary(process, 100.0, params, 600, steps,
                                 np.random.default_rng(seed))
    newsy = sto.simulate_summary(process, 100.0, params, 600, steps,
                                 np.random.default_rng(seed), modulation)
    return n_items, vol, quiet, newsy


@st.cache_data(ttl=900, show_spinner="Drawing short-rate paths…")
def _rates(r0: float, kappa: float, theta: float, years: int, seed: int):
    steps = 252 * years
    cols = np.unique(np.linspace(0, steps, min(260, steps + 1)).astype(int))
    vas = sto.vasicek_paths(r0, kappa, theta, 0.010, 600, steps, 252,
                            np.random.default_rng(seed))
    cir = sto.cir_paths(max(r0, 1e-4), kappa, max(theta, 1e-4), 0.06, 600, steps,
                        252, np.random.default_rng(seed))
    frame = pd.DataFrame({
        "Vasicek p50": np.percentile(vas[:, cols], 50, axis=0) * 100,
        "Vasicek p5": np.percentile(vas[:, cols], 5, axis=0) * 100,
        "Vasicek p95": np.percentile(vas[:, cols], 95, axis=0) * 100,
        "CIR p50": np.percentile(cir[:, cols], 50, axis=0) * 100,
        "CIR p5": np.percentile(cir[:, cols], 5, axis=0) * 100,
    }, index=cols / 252.0)
    frame.index.name = "Years"
    return frame, float(vas[:, -1].mean()), float((vas[:, -1] < 0).mean())


@st.fragment
def monte_carlo_panel() -> None:
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

    st.markdown("**Short rate (Vasicek / CIR)**")
    r1c, r2c, r3c = st.columns(3)
    r0 = r1c.slider("Starting short rate (%)", -1.0, 10.0, 2.5, 0.1) / 100
    kappa_r = r2c.slider("Mean-reversion speed κ", 0.1, 5.0, 1.2, 0.1)
    theta_r = r3c.slider("Long-run level θ (%)", -1.0, 8.0, 2.0, 0.1) / 100

    run = st.button("▶ Run simulation", type="primary",
                    help="Nothing heavy runs until you press this — a full sweep "
                         "at the largest settings takes a few seconds.")
    if run:
        st.session_state["mc_args"] = dict(
            process=process, mu=mu_pct / 100, sigma=sigma_pct / 100,
            years=years_mc, paths=n_paths, lam=jump_lambda, df=df_t,
            seed=int(mc_seed), r0=r0, kappa=kappa_r, theta=theta_r)

    args = st.session_state.get("mc_args")
    if not args:
        st.info("Set the parameters above, then press **Run simulation**.")
        return

    summary = _mc(args["process"], args["mu"], args["sigma"], args["years"],
                  args["paths"], args["lam"], args["df"], args["seed"])
    stats = summary["stats"]

    k = st.columns(5)
    k[0].metric("Median return", f"{stats['median_return']:.1%}")
    k[1].metric("Mean return", f"{stats['mean_return']:.1%}")
    k[2].metric("VaR 95%", f"{stats['var_95']:.1%}",
                help="5th percentile of the terminal return.")
    k[3].metric("Expected shortfall 95%", f"{stats['expected_shortfall_95']:.1%}",
                help="Average return in the worst 5% of paths.")
    k[4].metric("Probability of loss", f"{stats['prob_loss']:.0%}")

    # --- Fan chart: the distribution through time, not one path -------------
    fan = pd.DataFrame({f"p{q}": v for q, v in summary["fan"].items()},
                       index=summary["years"])
    fan.index.name = "Years"
    st.plotly_chart(
        ch.line(fan, title=f"Monte Carlo fan — {sto.PROCESSES[args['process']]}",
                height=380),
        width="stretch")
    st.caption(
        f"{args['paths']:,} paths. The p5–p95 band is the forward test's actual "
        "claim; the median line is not a forecast. Mean above median is the "
        "lognormal asymmetry — quoting the mean as 'expected outcome' is the "
        "classic error."
    )

    # --- Terminal distribution ---------------------------------------------
    d1, d2 = st.columns(2)
    with d1:
        counts, edges = np.histogram(summary["terminal_returns"], bins=40)
        centres = [f"{(edges[i] + edges[i + 1]) / 2:.0%}" for i in range(len(counts))]
        st.plotly_chart(
            ch.bar(centres, counts, title="Terminal return distribution", height=340),
            width="stretch")
    with d2:
        risk = pd.DataFrame({"Value": {
            "Median return": stats["median_return"],
            "Mean return": stats["mean_return"],
            "Std of return": stats["std_return"],
            "VaR 95%": stats["var_95"],
            "Expected shortfall 95%": stats["expected_shortfall_95"],
            "Worst path": stats["worst_return"],
            "Best path": stats["best_return"],
            "Deepest drawdown": stats["max_drawdown"],
        }})
        st.dataframe(risk.style.format("{:.2%}"), width="stretch", height=340)

    # --- Process comparison on identical parameters ------------------------
    st.divider()
    st.markdown("**Why the process choice matters**")
    st.caption(
        "Same drift, same volatility, same seed — only the law of the increments "
        "changes. The means agree by construction; the tails do not. That gap is "
        "the risk a pure-GBM forward test silently omits."
    )
    cmp_df = _compare(args["mu"], args["sigma"], args["years"],
                      min(args["paths"], 3_000), args["lam"], args["df"],
                      args["seed"])
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
    n_items, vol_mult, quiet, newsy = _news_effect(
        args["process"], args["mu"], args["sigma"], args["years"], args["lam"],
        args["df"], args["seed"])

    n1, n2 = st.columns(2)
    with n1:
        regime = pd.DataFrame({"Volatility multiplier": vol_mult},
                              index=np.arange(len(vol_mult)) / 252.0)
        regime.index.name = "Years"
        st.plotly_chart(
            ch.line(regime, title="News-driven volatility regime", height=320),
            width="stretch")
    with n2:
        band = pd.DataFrame({
            "No news p5": quiet["fan"][5], "No news p95": quiet["fan"][95],
            "With news p5": newsy["fan"][5], "With news p95": newsy["fan"][95],
        }, index=quiet["years"])
        band.index.name = "Years"
        st.plotly_chart(
            ch.line(band, title="Outcome band — news widens the distribution",
                    height=320),
            width="stretch")

    qs, ns = quiet["stats"], newsy["stats"]
    e = st.columns(4)
    e[0].metric("VaR 95 — no news", f"{qs['var_95']:.1%}")
    e[1].metric("VaR 95 — with news", f"{ns['var_95']:.1%}",
                delta=f"{(ns['var_95'] - qs['var_95']) * 100:.1f} pp")
    e[2].metric("News items drawn", f"{n_items:,}")
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
    rate_df, vas_mean, vas_neg = _rates(args["r0"], args["kappa"], args["theta"],
                                        args["years"], args["seed"])
    st.plotly_chart(ch.line(rate_df, title="Short-rate paths (%)", height=340),
                    width="stretch")
    rr = st.columns(3)
    rr[0].metric("Vasicek terminal mean", f"{vas_mean * 100:.2f}%")
    rr[1].metric("Negative-rate paths (Vasicek)", f"{vas_neg:.0%}")
    rr[2].metric("Feller satisfied (CIR)",
                 "yes" if sto.feller_condition(args["kappa"],
                                               max(args["theta"], 1e-4), 0.06) else "no",
                 help="2κθ ≥ σ² means CIR cannot reach zero.")


with tab_mc:
    monte_carlo_panel()

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
