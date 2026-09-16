"""Module 4 UI — S&P 500 supply chain graph and shock contagion."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.supply_chain_graph import (  # noqa: E402
    CompanyNode,
    EdgeProvenance,
    ShockScenario,
    SupplyChainGraph,
    SupplyEdge,
    extract_relations,
)

st.set_page_config(page_title="Supply Chain", page_icon="◆", layout="wide")
st.title("Module 4 — S&P 500 Supply Chain Graph Analytics")
st.caption(
    "Il contagio segue gli archi di fornitura, non la geografia. Il paese è "
    "un attributo di aggregazione, mai la topologia di propagazione."
)


@st.cache_resource
def demo_graph() -> SupplyChainGraph:
    """A hand-built semiconductor chain, used as a worked example.

    Real edges come from filing extraction; this seed graph exists so the
    propagation mechanics are inspectable without an EDGAR crawl.
    """
    nodes = [
        CompanyNode("TSM", "Taiwan Semiconductor", "Semis", "TW",
                    {"TW": 0.10, "US": 0.65, "CN": 0.25}, 5.0e11,
                    inventory_days=0, substitutability=0.05, beta=1.3),
        CompanyNode("ASML", "ASML Holding", "Semis Equipment", "NL",
                    {"NL": 0.05, "TW": 0.40, "CN": 0.30, "US": 0.25}, 3.0e11,
                    inventory_days=0, substitutability=0.02, beta=1.4),
        CompanyNode("NVDA", "NVIDIA", "Semis", "US",
                    {"US": 0.50, "TW": 0.20, "CN": 0.30}, 3.0e12,
                    inventory_days=60, substitutability=0.10, beta=1.7),
        CompanyNode("AMD", "AMD", "Semis", "US", {"US": 0.60, "CN": 0.40},
                    3.0e11, inventory_days=50, substitutability=0.15, beta=1.6),
        CompanyNode("AAPL", "Apple", "Hardware", "US",
                    {"US": 0.42, "CN": 0.19, "EU": 0.25, "JP": 0.14}, 3.4e12,
                    inventory_days=35, substitutability=0.25, beta=1.2),
        CompanyNode("DELL", "Dell Technologies", "Hardware", "US",
                    {"US": 0.70, "EU": 0.30}, 8.0e10,
                    inventory_days=25, substitutability=0.35, beta=1.1),
        CompanyNode("MSFT", "Microsoft", "Software", "US",
                    {"US": 0.50, "EU": 0.30, "ROW": 0.20}, 3.1e12,
                    inventory_days=120, substitutability=0.60, beta=0.9),
        CompanyNode("JPM", "JPMorgan Chase", "Financials", "US",
                    {"US": 0.75, "EU": 0.15, "ROW": 0.10}, 6.0e11,
                    inventory_days=365, substitutability=0.90, beta=1.1),
    ]
    edges = [
        SupplyEdge("ASML", "TSM", EdgeProvenance.OBSERVED_FILING, 0.95, 0.90,
                   "EUV lithography is sole-sourced from ASML."),
        SupplyEdge("TSM", "NVDA", EdgeProvenance.OBSERVED_FILING, 0.90, 0.90),
        SupplyEdge("TSM", "AMD", EdgeProvenance.OBSERVED_FILING, 0.85, 0.85),
        SupplyEdge("TSM", "AAPL", EdgeProvenance.OBSERVED_FILING, 0.75, 0.90),
        SupplyEdge("NVDA", "MSFT", EdgeProvenance.OBSERVED_FILING, 0.40, 0.75),
        SupplyEdge("NVDA", "DELL", EdgeProvenance.OBSERVED_FILING, 0.55, 0.80),
        SupplyEdge("AMD", "DELL", EdgeProvenance.INFERRED_IO, 0.30, 0.40),
        SupplyEdge("AAPL", "DELL", EdgeProvenance.INFERRED_IO, 0.05, 0.25),
    ]
    return SupplyChainGraph.from_edges(edges, nodes)


g = demo_graph()
cov = g.coverage()

a, b, c, d = st.columns(4)
a.metric("Nodi", cov["nodes"])
b.metric("Archi", cov["edges"])
c.metric(
    "Archi osservati", f"{cov['observed_edge_share']:.0%}",
    help="Quota di archi effettivamente dichiarati in un filing. Il resto è "
         "inferito da tavole input-output e supporta conclusioni più deboli.",
)
d.metric("Copertura geografia ricavi", f"{cov['revenue_geography_coverage']:.0%}")

if cov["observed_edge_share"] < 0.8:
    st.warning(
        f"Il {1 - cov['observed_edge_share']:.0%} degli archi è **inferito**, "
        "non dichiarato. Nel grafo sono tratteggiati e restano distinguibili: "
        "mescolarli con quelli osservati trasformerebbe una stima in un fatto."
    )

t1, t2, t3, t4, t5 = st.tabs(
    ["Contagio", "Rischio sistemico", "Grafo", "Estrazione da filing", "Geografia"]
)

with t1:
    st.subheader("Propagazione a due orologi")
    st.caption(
        "Il buffer di scorte è il punto: per settimane non accade nulla, poi "
        "la produzione cala di colpo. Senza buffer si ottiene una cascata "
        "immediata, che è il segno di un modello che non ha pensato al tempo fisico."
    )
    c1, c2 = st.columns([1, 2])
    with c1:
        # Default to the most systemically important node. Alphabetical order
        # would land on a weak leaf (AAPL has one 5%-dependence edge out), and
        # a first impression of "no contagion" reads as a broken tool rather
        # than as a correct answer about an unimportant node.
        ranked = sorted(g.g.nodes, key=lambda t: -g.systemic_importance().get(t, 0.0))
        seed = st.selectbox(
            "Nodo colpito", ranked, index=0,
            help="Ordinati per importanza sistemica decrescente.",
        )
        magnitude = st.slider("Intensità dello shock", 0.1, 1.0, 1.0, 0.05)
        horizon = st.slider("Orizzonte (giorni)", 90, 1095, 546, 7)
        transmission = st.slider("Trasmissione", 0.0, 1.0, 0.85, 0.05)
        recovery = st.slider("Recupero per tick", 0.0, 0.20, 0.02, 0.01)
        beta_shock = st.slider("Shock di mercato (indice)", -0.40, 0.10, -0.08, 0.01)

    try:
        scenario = ShockScenario(
            name=f"Shock su {seed}", seeds={seed: magnitude},
            horizon_days=horizon, tick_days=7, transmission=transmission,
            recovery_per_tick=recovery, market_beta_shock=beta_shock,
        )
        res = g.propagate_operational(scenario)
    except ValueError as exc:
        st.error(f"Scenario non valido: {exc}")
        st.stop()

    with c2:
        traj = pd.DataFrame(res.trajectory)
        traj.index = [i * scenario.tick_days for i in range(len(traj))]
        traj.index.name = "Giorni"
        st.line_chart(traj * 100, height=340)
        st.caption("Compromissione operativa % per nodo, nel tempo.")

    k1, k2, k3 = st.columns(3)
    k1.metric("Nodi colpiti", res.nodes_affected)
    k2.metric(
        "Moltiplicatore di contagio", f"{res.contagion_multiplier:.2f}x",
        help="Impatto totale diviso l'impatto del solo seed. Sopra 1 la rete "
             "amplifica.",
    )
    k3.metric("Secondo ordine", len(res.second_order_only()))

    st.subheader("Ordine della cascata")
    st.caption("È la sequenza che una visualizzazione temporale animerebbe.")
    order = res.cascade_order()
    if order:
        st.dataframe(
            pd.DataFrame(order, columns=["Ticker", "Giorno di innesco"]).set_index("Ticker"),
            width='stretch',
        )
    never = [t for t, dd in res.onset_days.items() if dd != dd]
    if never:
        st.success(
            f"Mai raggiunti entro l'orizzonte: **{', '.join(sorted(never))}** — "
            "buffer lunghi o alta sostituibilità assorbono lo shock."
        )
    for w in res.warnings:
        st.warning(w)

    st.divider()
    st.subheader("Stress di portafoglio")
    raw = st.text_area(
        "Pesi (TICKER=peso per riga)",
        "NVDA=0.30\nAAPL=0.30\nMSFT=0.25\nJPM=0.15", height=120,
    )
    weights: dict[str, float] = {}
    for line in raw.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            try:
                weights[k.strip().upper()] = float(v)
            except ValueError:
                st.caption(f"⚠️ peso non numerico ignorato: {line!r}")
    if weights:
        try:
            out = g.stress_portfolio(weights, scenario)
        except ValueError as exc:
            st.error(str(exc))
        else:
            s1, s2, s3 = st.columns(3)
            s1.metric("Rendimento atteso", f"{out['portfolio_return']:.2%}")
            s2.metric("Peso non coperto dal grafo", f"{out['uncovered_weight']:.0%}")
            s3.metric("Peggior contributo", out["worst_contributor"])
            st.dataframe(
                pd.Series({
                    k.replace("contrib_", ""): v * 100
                    for k, v in out.items() if k.startswith("contrib_")
                }).to_frame("Contributo %").style.format("{:.2f}"),
                width='stretch',
            )
            if out["uncovered_weight"] > 0:
                st.info(
                    "I titoli assenti dal grafo ricevono solo il termine "
                    "sistematico: il grafo incompleto **sottostima**, non ignora."
                )

with t2:
    st.subheader("Importanza sistemica")
    st.caption(
        "Centralità di Katz sul grafo invertito: un nodo pesa quando molti "
        "altri dipendono da lui, direttamente o transitivamente."
    )
    imp = g.systemic_importance()
    st.bar_chart(pd.Series(imp).sort_values(ascending=False), height=300)

    st.subheader("Punti singoli di rottura")
    spofs = g.single_points_of_failure(min_dependents=2, min_dependence=0.25)
    if spofs:
        st.dataframe(
            pd.DataFrame(spofs, columns=["Ticker", "Dipendenti", "Dipendenza media"])
              .set_index("Ticker").style.format({"Dipendenza media": "{:.1%}"}),
            width='stretch',
        )
        st.warning(
            f"**{spofs[0][0]}** è il collo di bottiglia: {spofs[0][1]} aziende "
            f"ne dipendono in media al {spofs[0][2]:.0%}."
        )
    else:
        st.info("Nessun collo di bottiglia sopra le soglie impostate.")

with t3:
    st.subheader("Grafo interattivo")
    st.caption(
        "Archi continui = osservati in filing. Tratteggiati = inferiti. "
        "La provenienza sopravvive nella visualizzazione."
    )
    if st.button("Genera grafo"):
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
            path = g.to_pyvis_html(tmp.name)
        if path is None:
            st.info("PyVis non installato: `pip install pyvis`.")
        else:
            st.components.v1.html(Path(path).read_text(), height=820)
    with st.expander("Archi in tabella"):
        st.dataframe(
            pd.DataFrame([{
                "Da": e.source, "A": e.target,
                "Dipendenza": e.dependence, "Provenienza": e.provenance.value,
                "Confidenza": e.confidence, "Evidenza": e.evidence or "—",
            } for e in g.edges()]),
            width='stretch',
        )

with t4:
    st.subheader("Estrazione relazioni da testo di filing")
    st.caption(
        "Regex su frasi-spia più NER opzionale. La disclosure è asimmetrica: "
        "i **clienti** sopra il 10% dei ricavi sono obbligatori (ASC 280), i "
        "**fornitori** sono in gran parte volontari — quindi quel lato del "
        "grafo resta più rado."
    )
    sample = (
        "Our largest customers include Apple Inc. and Dell Technologies, which "
        "together accounted for 34% of net revenues in fiscal 2025. We purchase "
        "substantially all of our advanced logic wafers from Taiwan "
        "Semiconductor Manufacturing Company. Item 1A. Risk Factors. We rely "
        "heavily upon a limited number of suppliers for certain components, "
        "including ASML Holding N.V. and Applied Materials Inc. The United "
        "States economy remained strong during the period."
    )
    text = st.text_area("Testo del filing", sample, height=200)
    use_spacy = st.checkbox("Usa spaCy NER (se installato)", False)
    min_conf = st.slider("Confidenza minima", 0.0, 1.0, 0.4, 0.05)

    roster = {
        "apple": "AAPL", "dell technologies": "DELL",
        "taiwan semiconductor manufacturing": "TSM", "asml": "ASML",
        "applied materials": "AMAT", "microsoft": "MSFT", "nvidia": "NVDA",
    }
    rels = extract_relations(text, known_companies=roster,
                            use_spacy=use_spacy, min_confidence=min_conf)
    if rels:
        st.dataframe(
            pd.DataFrame([{
                "Entità": n, "Relazione": r.value, "Confidenza": c, "Evidenza": ev,
            } for n, r, c, ev in sorted(rels, key=lambda x: -x[2])]),
            width='stretch',
        )
        st.caption(
            "Ogni arco conserva la frase da cui è stato estratto: un numero "
            "sorprendente si può risalire alla fonte."
        )
    else:
        st.info("Nessuna relazione estratta con questa soglia.")

with t5:
    st.subheader("Esposizione per geografia dei ricavi")
    st.caption(
        "Aggregata sui **ricavi**, non sulla sede legale. Apple non è "
        "«esposizione USA»: è ricavo globale con dipendenza produttiva cinese. "
        "Il dato sta nei segment disclosure che estrae il Modulo 5."
    )
    exp = g.country_exposure()
    if exp:
        st.bar_chart(pd.Series(exp) * 100, height=300)
        st.dataframe(
            pd.Series(exp).mul(100).to_frame("Esposizione %").style.format("{:.2f}"),
            width='stretch',
        )
    st.dataframe(
        pd.DataFrame([{
            "Ticker": t,
            "Paese (sede)": g.node(t).country,
            "Paese (ricavi)": g.node(t).primary_country,
            "Scorte (gg)": g.node(t).inventory_days,
            "Sostituibilità": g.node(t).substitutability,
            "Beta": g.node(t).beta,
        } for t in sorted(g.g.nodes)]).set_index("Ticker"),
        width='stretch',
    )
    st.info(
        "Nota la differenza fra le due colonne paese per TSM e ASML: è "
        "esattamente il motivo per cui il globo non può colorare per domicilio."
    )
