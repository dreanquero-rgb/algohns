"""Section 6 UI — forward stochastic world simulation.

Two surfaces, deliberately:

* the **interactive globe** is embedded as a self-contained HTML component,
  because Streamlit re-runs its script on every widget change and hosts
  components in an iframe — a per-tick animated globe driven from Python
  would stutter;
* the **Python analysis** below it is where Streamlit is actually good:
  running many seeds, tabulating distributions, comparing portfolios on an
  identical world.

The division mirrors the architecture: Python computes and validates, the
browser renders and animates.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.world_events import EVENT_CATALOGUE, expected_event_market_drift  # noqa: E402
from modules.world_export import DEFAULT_PORTFOLIO  # noqa: E402
from modules.world_forward import ForwardConfig, simulate_world  # noqa: E402
from modules.world_simulation import build_world  # noqa: E402
from modules.world_universe import COMPANIES  # noqa: E402

st.set_page_config(page_title="World Simulation", page_icon="◆", layout="wide")
st.title("Section 6 — World Simulation")
st.caption(
    "Test forward-looking: il mondo evolve secondo un generatore di eventi "
    "ponderato, le aziende crescono e falliscono, e il portafoglio viene "
    "valutato lungo il percorso."
)

st.warning(
    "**Non validato su episodi storici.** Serve per confrontare portafogli "
    "sullo *stesso* mondo simulato, non come previsione. Il dataset è curato "
    "a mano: le percentuali di dipendenza sono assunzioni di modello."
)

GLOBE = ROOT.parent / "public" / "world" / "index.html"


@st.cache_resource
def world_graph():
    return build_world()


@st.cache_data(ttl=1800, show_spinner="Simulo il mondo…")
def run_many(years: int, intensity: float, n_seeds: int, weights_key: tuple):
    """Many worlds, one portfolio. Returns per-seed outcomes."""
    graph = world_graph()
    weights = dict(weights_key)
    rows = []
    for seed in range(n_seeds):
        tl = simulate_world(
            graph,
            ForwardConfig(horizon_days=365 * years, seed=seed,
                          event_intensity=intensity),
        )
        path = tl.portfolio_path(weights)
        arr = np.array(path)
        eq = 1.0 + arr
        dd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
        rows.append({
            "seed": seed,
            "Portafoglio %": arr[-1] * 100,
            "Mercato %": (tl.market_index[-1] / tl.market_index[0] - 1) * 100,
            "Max DD %": dd * 100,
            "Fallimenti": len(tl.bankruptcies),
            "Eventi": len(tl.events),
        })
    return pd.DataFrame(rows)


tab_globe, tab_dist, tab_compare, tab_cat = st.tabs(
    ["Globo interattivo", "Distribuzione esiti", "Confronto portafogli", "Catalogo eventi"]
)

with tab_globe:
    if GLOBE.exists():
        st.caption(
            "Trascina per ruotare, doppio clic per fermare la rotazione. "
            "Il portafoglio è modificabile **durante** la simulazione: il mondo "
            "non dipende da cosa detieni, quindi la rivalutazione è immediata."
        )
        st.components.v1.html(GLOBE.read_text(), height=900, scrolling=True)
    else:
        st.error(
            f"Globo non trovato in {GLOBE}. Rigeneralo con "
            "`PYTHONPATH=platform python platform/build_world.py`."
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
    n_seeds = c3.slider("Numero di mondi", 10, 120, 40, 10)

    st.caption("Portafoglio valutato (modificabile nella tab «Confronto»).")
    frame = run_many(years, intensity, n_seeds, tuple(sorted(DEFAULT_PORTFOLIO.items())))

    a, b, c, d = st.columns(4)
    a.metric("Portafoglio (mediana)", f"{frame['Portafoglio %'].median():.1f}%")
    b.metric("Mercato (mediana)", f"{frame['Mercato %'].median():.1f}%")
    c.metric("Max DD (mediana)", f"{frame['Max DD %'].median():.1f}%")
    d.metric("Mondi in perdita", f"{(frame['Portafoglio %'] < 0).mean():.0%}")

    st.bar_chart(
        frame.set_index("seed")[["Portafoglio %", "Mercato %"]], height=280
    )
    q = frame[["Portafoglio %", "Mercato %", "Max DD %", "Fallimenti"]].describe(
        percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]
    )
    st.dataframe(q.style.format("{:.2f}"), width="stretch")

    geometric = 0.07 - 0.16**2 / 2
    st.caption(
        f"Riferimento: la mediana di un GBM segue il drift **geometrico** "
        f"({geometric:.2%}/anno = {((1 + geometric) ** years - 1):.1%} su "
        f"{years} anni), non quello aritmetico. Confrontare con il drift "
        "aritmetico composto fa sembrare rotta una calibrazione corretta."
    )

with tab_compare:
    st.subheader("Due portafogli, lo stesso mondo")
    st.caption(
        "Il mondo è indipendente dal portafoglio, quindi entrambi corrono sul "
        "medesimo percorso: il confronto isola il portafoglio, non la fortuna."
    )
    tickers = sorted(c.ticker for c in COMPANIES)
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Portafoglio A**")
        a_names = st.multiselect("Titoli A", tickers,
                                 default=list(DEFAULT_PORTFOLIO)[:8], key="pa")
    with col_b:
        st.markdown("**Portafoglio B**")
        b_names = st.multiselect("Titoli B", tickers,
                                 default=["JNJ", "KO", "PG", "NEE", "WMT", "UNH"],
                                 key="pb")

    seed = st.number_input("Seed del mondo", 1, 99999, 2026, step=1)
    yrs = st.slider("Orizzonte (anni)", 1, 10, 5, key="cmp_yrs")

    if not a_names or not b_names:
        st.info("Seleziona almeno un titolo per ciascun portafoglio.")
    else:
        tl = simulate_world(
            world_graph(),
            ForwardConfig(horizon_days=365 * yrs, seed=int(seed)),
        )
        pa = tl.portfolio_path({t: 1 / len(a_names) for t in a_names})
        pb = tl.portfolio_path({t: 1 / len(b_names) for t in b_names})
        curve = pd.DataFrame({
            "A": np.array(pa) * 100,
            "B": np.array(pb) * 100,
            "Mercato": (np.array(tl.market_index) / tl.market_index[0] - 1) * 100,
        }, index=tl.calendar_days)
        curve.index.name = "Giorni"
        st.line_chart(curve, height=360)

        m1, m2, m3 = st.columns(3)
        m1.metric("A finale", f"{pa[-1] * 100:.1f}%")
        m2.metric("B finale", f"{pb[-1] * 100:.1f}%")
        m3.metric("Differenza", f"{(pa[-1] - pb[-1]) * 100:+.1f} pp")

        if tl.bankruptcies:
            st.error(
                "Fallimenti in questo mondo: "
                + ", ".join(f"{k} (g{v})" for k, v in sorted(
                    tl.bankruptcies.items(), key=lambda kv: kv[1]))
            )
        for w in tl.warnings:
            st.caption(f"⚠️ {w}")

        st.markdown("**Notiziario del mondo**")
        news = tl.news_for({t: 1 / len(a_names) for t in a_names})
        st.dataframe(
            pd.DataFrame([{
                "Giorno": n["day"], "Categoria": n["category"],
                "Rilevanza": n["portfolioRelevance"], "Gravità": n["severity"],
                "Titolo": n["headline"],
            } for n in news[:200]]),
            width="stretch", height=320,
        )

with tab_cat:
    st.subheader("Catalogo degli eventi")
    st.caption(
        "Le intensità sono tassi annui calibrati su base rate storiche "
        "approssimative — giudizi di ordine di grandezza, non parametri "
        "stimati. Sono la prima cosa da rivedere quando esisterà l'harness "
        "di validazione storica."
    )
    drag = expected_event_market_drift()
    st.metric(
        "Contributo netto del catalogo al mercato", f"{drag:.2%}/anno",
        help="Compensato dal simulatore, così il drift dichiarato resta "
             "l'attesa incondizionata. Senza compensazione ogni mondo "
             "scenderebbe a prescindere.",
    )
    st.dataframe(
        pd.DataFrame([{
            "Chiave": t.key,
            "Categoria": t.category.value,
            "Ambito": t.scope.value,
            "Tasso/anno": t.annual_rate,
            "Favorevole": "sì" if t.favourable else "",
            "Gravità": t.severity_label,
            "Impairment": f"{t.impairment[0]:.0%}–{t.impairment[1]:.0%}",
            "Shock equity": f"{t.equity_shock[0]:+.0%}–{t.equity_shock[1]:+.0%}",
            "Shock mercato": f"{t.market_shock[0]:+.0%}–{t.market_shock[1]:+.0%}",
            "Durata (gg)": f"{t.duration_days[0]}–{t.duration_days[1]}",
        } for t in sorted(EVENT_CATALOGUE, key=lambda x: (x.category.value, -x.annual_rate))]),
        width="stretch", height=560,
    )
