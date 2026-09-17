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
    "Test forward-looking: il mondo evolve, le aziende crescono e falliscono, "
    "e il portafoglio viene valutato lungo il percorso.",
    badge="Module 6",
)

st.warning(
    "**Non validato su episodi storici.** Serve per confrontare portafogli "
    "sullo *stesso* mondo simulato, non come previsione. Il dataset è curato "
    "a mano: le percentuali di dipendenza sono assunzioni di modello, non "
    "dati dichiarati."
)

GLOBE = Path(__file__).resolve().parent.parent.parent / "public" / "world" / "index.html"


@st.cache_resource(show_spinner=False)
def _graph():
    return build_world()


@st.cache_data(ttl=1800, show_spinner="Simulo i mondi…")
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


@st.cache_data(ttl=1800, show_spinner="Simulo il mondo…")
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
    ["Globo interattivo", "Distribuzione esiti", "Confronto portafogli", "Catalogo eventi"]
)

with tab_globe:
    if GLOBE.exists():
        st.caption(
            "Trascina per ruotare, doppio clic per fermare la rotazione. Il "
            "portafoglio è modificabile **durante** la simulazione: il mondo "
            "non dipende da cosa detieni, quindi la rivalutazione è immediata."
        )
        st.iframe(GLOBE.read_text(), height=920)
    else:
        st.error(
            f"Globo non trovato in `{GLOBE}`. Rigeneralo con "
            "`python scripts/build_world.py`."
        )

with tab_dist:
    st.subheader("Esiti su molti mondi")
    st.caption(
        "Un singolo mondo è un aneddoto. La distribuzione su molti seed è "
        "l'unica lettura che dice qualcosa sul portafoglio."
    )
    c1, c2, c3 = st.columns(3)
    years = c1.slider("Orizzonte (anni)", 1, 10, 5)
    intensity = c2.slider("Turbolenza", 0.2, 3.0, 1.0, 0.1)
    n_seeds = c3.slider("Numero di mondi", 10, 100, 30, 10)

    frame = _many(years, intensity, n_seeds,
                  tuple(sorted(DEFAULT_PORTFOLIO.items())))

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Portafoglio (mediana)", f"{frame['Portfolio %'].median():.1f}%")
    k2.metric("Mercato (mediana)", f"{frame['Market %'].median():.1f}%")
    k3.metric("Max DD (mediana)", f"{frame['Max DD %'].median():.1f}%")
    k4.metric("Mondi in perdita", f"{(frame['Portfolio %'] < 0).mean():.0%}")

    st.plotly_chart(
        ch.grouped_bar(
            frame.set_index("seed")[["Portfolio %", "Market %"]],
            title="Esito per mondo simulato", height=340,
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
        f"Riferimento: la mediana di un GBM segue il drift **geometrico** "
        f"({geometric:.2%}/anno = {((1 + geometric) ** years - 1):.1%} su "
        f"{years} anni), non quello aritmetico. Confrontare col drift "
        "aritmetico composto fa sembrare rotta una calibrazione corretta."
    )

with tab_cmp:
    st.subheader("Due portafogli, lo stesso mondo")
    st.caption(
        "Il mondo è indipendente dal portafoglio, quindi entrambi corrono sul "
        "medesimo percorso: il confronto isola il portafoglio, non la fortuna."
    )
    tickers = sorted(c.ticker for c in COMPANIES)
    ca, cb = st.columns(2)
    a_names = ca.multiselect("Portafoglio A", tickers,
                             default=list(DEFAULT_PORTFOLIO)[:8], key="w6a")
    b_names = cb.multiselect("Portafoglio B", tickers,
                             default=["JNJ", "KO", "PG", "NEE", "WMT", "UNH"],
                             key="w6b")
    d1, d2, d3 = st.columns(3)
    seed = d1.number_input("Seed del mondo", 1, 99999, 2026, step=1)
    yrs = d2.slider("Orizzonte (anni)", 1, 10, 5, key="w6y")
    inten = d3.slider("Turbolenza", 0.2, 3.0, 1.0, 0.1, key="w6i")

    if not a_names or not b_names:
        st.info("Seleziona almeno un titolo per ciascun portafoglio.")
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
        curve.index.name = "Giorni"
        st.plotly_chart(
            ch.line(curve, title="Percorso sullo stesso mondo", height=380,
                    yfmt=".1f"),
            width="stretch",
        )
        m1, m2, m3 = st.columns(3)
        m1.metric("A finale", f"{pa[-1] * 100:.1f}%")
        m2.metric("B finale", f"{pb[-1] * 100:.1f}%")
        m3.metric("Differenza", f"{(pa[-1] - pb[-1]) * 100:+.1f} pp")

        if fails:
            st.error(
                "Fallimenti in questo mondo: "
                + ", ".join(f"{k} (g{v})" for k, v in sorted(fails.items(),
                                                             key=lambda kv: kv[1]))
            )
        for w in warns:
            st.caption(f"⚠️ {w}")

        st.markdown("**Notiziario del mondo** — tutto, non solo il portafoglio.")
        st.dataframe(
            pd.DataFrame([{
                "Giorno": n["day"], "Categoria": n["category"],
                "Rilevanza": n["portfolioRelevance"], "Gravità": n["severity"],
                "Titolo": n["headline"],
            } for n in news[:250]]),
            width="stretch", height=340,
        )

with tab_cat:
    st.subheader("Catalogo degli eventi")
    st.caption(
        "Intensità come tassi annui calibrati su base rate storiche "
        "approssimative: giudizi di ordine di grandezza, non parametri "
        "stimati. Sono la prima cosa da rivedere quando esisterà l'harness "
        "di validazione storica."
    )
    drag = expected_event_market_drift()
    fav = sum(1 for t in EVENT_CATALOGUE if t.favourable)
    q1, q2, q3 = st.columns(3)
    q1.metric("Template", len(EVENT_CATALOGUE))
    q2.metric("Favorevoli", f"{fav / len(EVENT_CATALOGUE):.0%}",
              help="Un generatore di soli disastri produce un mondo in cui "
                   "ogni portafoglio perde: un test che non si può vincere "
                   "non insegna nulla.")
    q3.metric("Contributo netto al mercato", f"{drag:.2%}/anno",
              help="Compensato dal simulatore, così il drift dichiarato resta "
                   "l'attesa incondizionata.")
    st.dataframe(
        pd.DataFrame([{
            "Chiave": t.key, "Categoria": t.category.value,
            "Ambito": t.scope.value, "Tasso/anno": t.annual_rate,
            "Favorevole": "sì" if t.favourable else "",
            "Gravità": t.severity_label,
            "Impairment": f"{t.impairment[0]:.0%}–{t.impairment[1]:.0%}",
            "Shock equity": f"{t.equity_shock[0]:+.0%}–{t.equity_shock[1]:+.0%}",
            "Shock mercato": f"{t.market_shock[0]:+.0%}–{t.market_shock[1]:+.0%}",
            "Durata (gg)": f"{t.duration_days[0]}–{t.duration_days[1]}",
        } for t in sorted(EVENT_CATALOGUE,
                          key=lambda x: (x.category.value, -x.annual_rate))]),
        width="stretch", height=520,
    )
