"""SECTION 6 — World Simulation.

A time-stepped scenario simulator over the real supply-chain graph, with a
globe as its display surface.

Architecture, and the reason for it: this module **precomputes** whole
scenarios in Python and exports a compact JSON payload. The browser then
plays the timeline back and renders it. Streamlit re-runs its script on
every widget interaction and hosts 3D components in an iframe, so driving an
animated tick loop from the Python side would be sluggish; and Cloudflare
Workers have no Python runtime, so the engine cannot live at the edge
either. Precompute-then-play puts the heavy maths where it belongs and keeps
the animation at browser frame rate.

The propagation model is not re-implemented here — it is
`SupplyChainGraph.propagate_operational`, the same two-clock engine module 4
tests. This module's job is to build the world, run scenarios over it, and
shape the result for a renderer:

* nodes placed at **operating-centre** coordinates, not domicile
* country aggregation weighted by **revenue geography**, not domicile
* arcs carrying provenance, so inferred edges stay visually distinguishable
* per-tick state for playback, plus the portfolio impact at each tick
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from modules.supply_chain_graph import (
    CompanyNode,
    EdgeProvenance,
    PropagationResult,
    ShockScenario,
    SupplyChainGraph,
    SupplyEdge,
)
from modules.world_universe import COMPANIES, COUNTRIES, SUPPLY_LINKS

__all__ = [
    "build_world",
    "WorldSimulation",
    "SCENARIO_LIBRARY",
    "ScenarioSpec",
    "export_payload",
]


def build_world() -> SupplyChainGraph:
    """Assemble the world graph from the curated dataset."""
    nodes = [
        CompanyNode(
            ticker=c.ticker,
            name=c.name,
            sector=c.sector,
            country=c.domicile,
            revenue_geography=dict(c.revenue_geography),
            market_cap=c.market_cap_usd,
            inventory_days=c.inventory_days,
            substitutability=c.substitutability,
            beta=c.beta,
        )
        for c in COMPANIES
    ]
    edges = [
        SupplyEdge(
            source=src,
            target=dst,
            provenance=(
                EdgeProvenance.OBSERVED_FILING if observed
                else EdgeProvenance.INFERRED_IO
            ),
            dependence=dep,
            confidence=0.85 if observed else 0.40,
        )
        for src, dst, dep, observed in SUPPLY_LINKS
    ]
    return SupplyChainGraph.from_edges(edges, nodes)


@dataclass(frozen=True)
class ScenarioSpec:
    """A named, pre-parameterised world event.

    Parameters are financial and physical — which nodes, how hard, how long,
    how fast it transmits. Deliberately not RPG-style upgrade points: a
    scenario has to be arguable, and eventually falsifiable against a
    historical episode.
    """

    key: str
    title: str
    seeds: dict[str, float]
    horizon_days: int
    market_beta_shock: float
    transmission: float = 0.85
    recovery_per_tick: float = 0.02
    narrative: str = ""
    historical_analogue: str = ""

    def to_shock(self, *, tick_days: int = 7) -> ShockScenario:
        return ShockScenario(
            name=self.title,
            seeds=dict(self.seeds),
            horizon_days=self.horizon_days,
            tick_days=tick_days,
            transmission=self.transmission,
            recovery_per_tick=self.recovery_per_tick,
            market_beta_shock=self.market_beta_shock,
            description=self.narrative,
        )


SCENARIO_LIBRARY: list[ScenarioSpec] = [
    ScenarioSpec(
        key="taiwan_strait",
        title="Chiusura dello Stretto di Taiwan",
        seeds={"TSM": 1.0, "2317.TW": 0.85},
        horizon_days=728,
        market_beta_shock=-0.22,
        recovery_per_tick=0.015,
        narrative=(
            "Blocco totale della capacità di fonderia taiwanese e del "
            "contract manufacturing. È il chokepoint più stretto "
            "dell'economia mondiale: nessuna alternativa a breve per i "
            "nodi avanzati."
        ),
        historical_analogue="Nessun precedente diretto; il 2021 chip shortage è il limite inferiore.",
    ),
    ScenarioSpec(
        key="asml_export_ban",
        title="Blocco export litografia EUV",
        seeds={"ASML": 0.75},
        horizon_days=728,
        market_beta_shock=-0.10,
        narrative=(
            "ASML è monopolista EUV. Lo shock colpisce a monte: non ferma "
            "la produzione odierna, ferma l'espansione di capacità — "
            "quindi si vede con ritardo lungo, ma è difficilissimo da "
            "sostituire."
        ),
        historical_analogue="Controlli export USA-Paesi Bassi verso la Cina, 2023.",
    ),
    ScenarioSpec(
        key="suez_closure",
        title="Chiusura del Canale di Suez",
        seeds={"MAERSK": 0.70},
        horizon_days=273,
        market_beta_shock=-0.05,
        recovery_per_tick=0.05,
        narrative=(
            "Interruzione logistica. Il rerouting via Capo di Buona "
            "Speranza aggiunge ~10-14 giorni: colpisce le scorte, non la "
            "capacità produttiva, quindi recupera più in fretta."
        ),
        historical_analogue="Ever Given, marzo 2021; crisi Mar Rosso 2023-24.",
    ),
    ScenarioSpec(
        key="energy_shock",
        title="Shock energetico globale",
        seeds={"2222.SR": 0.55, "XOM": 0.35, "SHEL": 0.35},
        horizon_days=546,
        market_beta_shock=-0.18,
        recovery_per_tick=0.03,
        narrative=(
            "Riduzione dell'offerta di greggio. L'energia entra in tutto "
            "ciò che si muove o si fonde: la propagazione è ampia ma "
            "diluita, non profonda su pochi nodi."
        ),
        historical_analogue="2022 post-invasione; 1973 come limite superiore.",
    ),
    ScenarioSpec(
        key="lithium_squeeze",
        title="Stretta sul litio",
        seeds={"SQM": 0.80},
        horizon_days=546,
        market_beta_shock=-0.04,
        narrative=(
            "Collo di bottiglia su una materia prima concentrata "
            "geograficamente. Colpisce duramente pochi nodi (auto "
            "elettriche, batterie) e lascia intatto il resto."
        ),
        historical_analogue="Picco prezzi litio 2022.",
    ),
    ScenarioSpec(
        key="industrial_gas",
        title="Interruzione gas industriali",
        seeds={"LIN": 0.65},
        horizon_days=364,
        market_beta_shock=-0.06,
        narrative=(
            "Il caso istruttivo: un fornitore piccolo in capitalizzazione "
            "con raggio d'azione ampio. I gas ultra-puri sono input non "
            "sostituibili per fonderie e farmaceutica."
        ),
        historical_analogue="Carenza di neon da Ucraina, 2022.",
    ),
    ScenarioSpec(
        key="pandemic",
        title="Pandemia — arresto manifatturiero",
        seeds={
            "2317.TW": 0.80, "005930.KS": 0.55, "BOSCH": 0.60,
            "MAERSK": 0.55, "TM": 0.50,
        },
        horizon_days=546,
        market_beta_shock=-0.34,
        recovery_per_tick=0.035,
        narrative=(
            "Shock multi-seed simultaneo su manifattura e logistica. "
            "È lo scenario più vicino a un episodio storico documentato, "
            "quindi il primo candidato per la validazione."
        ),
        historical_analogue="COVID-19, febbraio-marzo 2020 (drawdown S&P -34%).",
    ),
]


@dataclass
class WorldSimulation:
    """Runs scenarios over the world graph and shapes them for a renderer."""

    graph: SupplyChainGraph = field(default_factory=build_world)
    tick_days: int = 7

    def run(self, spec: ScenarioSpec) -> PropagationResult:
        return self.graph.propagate_operational(spec.to_shock(tick_days=self.tick_days))

    def country_impact(self, snapshot: dict[str, float]) -> dict[str, float]:
        """Aggregate node impairment to countries by revenue geography.

        Market-cap weighted, so a 60% hit to a trillion-dollar company
        outweighs a wipeout at a small one. This is the number the globe
        colours a country with — and it is computed from where revenue
        comes from, not where the company is registered.
        """
        weighted: dict[str, float] = {}
        totals: dict[str, float] = {}
        for ticker, impairment in snapshot.items():
            node = self.graph.node(ticker)
            cap = node.market_cap or 1.0
            geo = node.revenue_geography or (
                {node.country: 1.0} if node.country else {}
            )
            for country, share in geo.items():
                w = cap * share
                weighted[country] = weighted.get(country, 0.0) + impairment * w
                totals[country] = totals.get(country, 0.0) + w
        return {
            c: (weighted[c] / totals[c] if totals[c] > 0 else 0.0)
            for c in weighted
        }

    def portfolio_path(
        self,
        result: PropagationResult,
        weights: dict[str, float],
        *,
        impairment_to_return: float = -0.8,
    ) -> list[float]:
        """Portfolio return at each tick.

        The market channel is applied in full from tick 0 — equities reprice
        on the news, they do not wait for inventories to run out — while the
        idiosyncratic component tracks impairment as it actually develops.
        That split is the two-clock model showing up in the P&L.
        """
        total = sum(weights.values())
        if total <= 0:
            raise ValueError("i pesi di portafoglio sommano a zero")
        beta_shock = result.scenario.market_beta_shock

        path: list[float] = []
        for tick, snapshot in enumerate(result.trajectory):
            value = 0.0
            for ticker, w in weights.items():
                share = w / total
                if ticker in self.graph.g:
                    node = self.graph.node(ticker)
                    systematic = node.beta * beta_shock
                    idio = snapshot.get(ticker, 0.0) * impairment_to_return
                    value += share * (systematic + idio)
                else:
                    value += share * beta_shock
            path.append(value)
        return path

    def export(
        self,
        specs: list[ScenarioSpec] | None = None,
        *,
        portfolio: dict[str, float] | None = None,
    ) -> dict:
        """Full payload: world geometry plus every scenario precomputed."""
        specs = specs or SCENARIO_LIBRARY
        portfolio = portfolio or _default_portfolio()

        nodes = []
        for spec in COMPANIES:
            node = self.graph.node(spec.ticker)
            nodes.append({
                "id": spec.ticker,
                "name": spec.name,
                "sector": spec.sector,
                "domicile": spec.domicile,
                "lat": spec.lat,
                "lon": spec.lon,
                "cap": spec.market_cap_usd,
                "inventoryDays": node.inventory_days,
                "substitutability": node.substitutability,
                "beta": node.beta,
                "revenueGeography": node.revenue_geography,
                "primaryCountry": node.primary_country,
            })

        importance = self.graph.systemic_importance()
        peak = max(importance.values()) if importance else 1.0
        for n in nodes:
            n["systemic"] = round(
                importance.get(n["id"], 0.0) / peak if peak else 0.0, 4
            )

        by_ticker = {c.ticker: c for c in COMPANIES}
        edges = [
            {
                "source": e.source,
                "target": e.target,
                "dependence": e.dependence,
                "observed": e.provenance.is_observed,
                "sourceLat": by_ticker[e.source].lat,
                "sourceLon": by_ticker[e.source].lon,
                "targetLat": by_ticker[e.target].lat,
                "targetLon": by_ticker[e.target].lon,
            }
            for e in self.graph.edges()
            if e.source in by_ticker and e.target in by_ticker
        ]

        scenarios = []
        for spec in specs:
            result = self.run(spec)
            pf = self.portfolio_path(result, portfolio)
            scenarios.append({
                "key": spec.key,
                "title": spec.title,
                "narrative": spec.narrative,
                "historicalAnalogue": spec.historical_analogue,
                "seeds": spec.seeds,
                "tickDays": self.tick_days,
                "horizonDays": spec.horizon_days,
                "marketBetaShock": spec.market_beta_shock,
                "amplification": round(result.contagion_multiplier, 3),
                "nodesAffected": result.nodes_affected,
                # Rounded to 4dp: the payload shrinks by roughly half and no
                # display needs more precision than that.
                "ticks": [
                    {k: round(v, 4) for k, v in snap.items() if v > 1e-4}
                    for snap in result.trajectory
                ],
                "countryTicks": [
                    {k: round(v, 4) for k, v in self.country_impact(snap).items()
                     if v > 1e-4}
                    for snap in result.trajectory
                ],
                "portfolioPath": [round(v, 5) for v in pf],
                "cascade": [
                    {"id": t, "day": int(d)} for t, d in result.cascade_order()
                ],
                "peakImpairment": {
                    k: round(v, 4) for k, v in result.peak_impairment.items()
                    if v > 1e-4
                },
                "neverReached": sorted(
                    t for t, d in result.onset_days.items() if d != d
                ),
                "warnings": result.warnings,
            })

        return {
            "meta": {
                "generator": "algohns-world-simulation",
                "tickDays": self.tick_days,
                "observedEdgeShare": round(self.graph.observed_share, 4),
                "coverage": self.graph.coverage(),
                "dataProvenance": (
                    "Dataset curato a mano. Domicili, sedi operative e forma "
                    "generale della geografia dei ricavi da conoscenza "
                    "pubblica; le percentuali di dipendenza sono assunzioni "
                    "di modello, non dati dichiarati."
                ),
                "notValidated": (
                    "La propagazione non è ancora stata confrontata con "
                    "episodi storici. I numeri di secondo ordine sono "
                    "indicativi."
                ),
            },
            "countries": [
                {
                    "code": c.code, "name": c.name,
                    "lat": c.lat, "lon": c.lon, "region": c.region,
                }
                for c in COUNTRIES.values()
            ],
            "nodes": nodes,
            "edges": edges,
            "portfolio": portfolio,
            "scenarios": scenarios,
        }


def _default_portfolio() -> dict[str, float]:
    """A tech-tilted global portfolio, used as the demo book."""
    return {
        "AAPL": 0.12, "MSFT": 0.12, "NVDA": 0.10, "GOOGL": 0.08,
        "AMZN": 0.07, "TSM": 0.06, "ASML": 0.05, "TSLA": 0.04,
        "JPM": 0.07, "JNJ": 0.06, "LLY": 0.05, "XOM": 0.05,
        "NESN.SW": 0.04, "SIE.DE": 0.04, "KO": 0.03, "ENEL.MI": 0.02,
    }


def export_payload(path: str | Path | None = None, **kw) -> dict:
    """Build the payload, optionally writing it to `path`."""
    payload = WorldSimulation().export(**kw)
    if path is not None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, separators=(",", ":")))
    return payload
