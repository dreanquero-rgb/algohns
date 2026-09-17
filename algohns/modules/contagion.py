"""Supply-chain contagion physics.

Separated from ``supply_chain_graph`` on purpose: that module *mines* filings
for relationships and renders them, while this one *propagates shocks* through
a graph once you have it. Different responsibilities, so different modules —
and the World Simulation needs only this half.

Three design choices decide whether the output is a risk tool or a picture:

**1. Contagion follows edges, not geography.** Shocks travel along balance-sheet
and supply relationships. A Taiwan->Cupertino edge matters more than an
Italy->Slovenia border. Country is an *aggregation* attribute for display,
never the propagation topology.

**2. Observed and inferred edges stay distinguishable.** Disclosure is
asymmetric: ASC 280 requires naming *customers* above 10% of revenue, while
*supplier* naming is largely voluntary. Filling gaps from input-output tables
is legitimate; mixing an inferred edge with a disclosed one without labelling
it launders a guess into a fact. Every edge carries ``provenance``, and an
observed edge always beats an inferred one regardless of confidence.

**3. Two clocks, not one.** Market repricing moves in days; physical supply
disruption burns through inventory for weeks before it touches production. A
single tick rate collapses that distinction and shows a Suez closure hitting
earnings the next morning. The inventory buffer is modelled explicitly.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

import networkx as nx

__all__ = [
    "EdgeProvenance",
    "SupplyEdge",
    "CompanyNode",
    "SupplyChainGraph",
    "ShockScenario",
    "PropagationResult",
]

log = logging.getLogger(__name__)


class EdgeProvenance(str, Enum):
    """Where an edge came from. Never collapse these in a visualisation."""

    OBSERVED_FILING = "observed_filing"   # named in a 10-K/10-Q
    INFERRED_IO = "inferred_io"           # imputed from input-output tables
    MANUAL = "manual"                     # analyst-entered

    @property
    def is_observed(self) -> bool:
        return self is EdgeProvenance.OBSERVED_FILING


@dataclass(frozen=True)
class SupplyEdge:
    """A directed dependency: `source` supplies `target`."""

    source: str
    target: str
    provenance: EdgeProvenance
    # Share of target's inputs (or revenue) depending on source, in [0, 1].
    dependence: float = 0.05
    confidence: float = 0.5
    evidence: str = ""
    filing_url: str = ""
    form: str = ""
    filed: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.dependence <= 1.0:
            raise ValueError(f"dependence fuori range [0,1]: {self.dependence}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence fuori range [0,1]: {self.confidence}")
        if self.source == self.target:
            raise ValueError(f"self-loop non ammesso su {self.source}")


@dataclass
class CompanyNode:
    """A company, with the attributes propagation and display need.

    `revenue_geography` is the correct country mapping, not the HQ
    domicile: Apple is not "US exposure", it is global revenue with Chinese
    manufacturing dependence. Segment disclosures (module 5) are the source.
    """

    ticker: str
    name: str = ""
    sector: str = ""
    country: str = ""                     # domicile, for reference only
    revenue_geography: dict[str, float] = field(default_factory=dict)
    market_cap: float = 0.0
    # Days of inventory before an input disruption reaches production.
    inventory_days: float = 45.0
    # Ability to re-source, in [0, 1]. 1.0 absorbs any supplier loss.
    substitutability: float = 0.3
    beta: float = 1.0                     # for the fast market channel

    def __post_init__(self) -> None:
        if not 0.0 <= self.substitutability <= 1.0:
            raise ValueError(
                f"{self.ticker}: substitutability fuori range [0,1]"
            )
        if self.inventory_days < 0:
            raise ValueError(f"{self.ticker}: inventory_days negativo")

    @property
    def primary_country(self) -> str:
        """Largest revenue geography, falling back to domicile."""
        if self.revenue_geography:
            return max(self.revenue_geography.items(), key=lambda kv: kv[1])[0]
        return self.country


# ------------------------------------------------------------- shock models


@dataclass
class ShockScenario:
    """A named shock, parameterised in financial terms.

    Deliberately not RPG-style upgrade points: the inputs are things an
    analyst can defend (which nodes, how hard, how long, how fast it
    transmits), so a scenario can be argued about and validated against a
    historical episode.
    """

    name: str
    # ticker -> initial impairment in [0, 1]
    seeds: dict[str, float]
    horizon_days: int = 180
    tick_days: int = 7
    # Fraction of a supplier's impairment passed to a dependent per unit
    # dependence. Below 1.0 the cascade attenuates.
    transmission: float = 0.85
    # Per-tick recovery of an impaired node.
    recovery_per_tick: float = 0.04
    market_beta_shock: float = 0.0     # index-level return shock for the fast channel
    description: str = ""

    def __post_init__(self) -> None:
        if not self.seeds:
            raise ValueError("uno scenario richiede almeno un seed")
        bad = {k: v for k, v in self.seeds.items() if not 0.0 <= v <= 1.0}
        if bad:
            raise ValueError(f"impairment iniziale fuori range [0,1]: {bad}")
        if self.tick_days <= 0:
            raise ValueError("tick_days deve essere positivo")
        if self.horizon_days < self.tick_days:
            raise ValueError("horizon_days deve coprire almeno un tick")
        if not 0.0 <= self.transmission <= 1.0:
            raise ValueError(f"transmission fuori range [0,1]: {self.transmission}")

    @property
    def n_ticks(self) -> int:
        return self.horizon_days // self.tick_days


@dataclass
class PropagationResult:
    """Per-tick impairment trajectory for every node."""

    scenario: ShockScenario
    # tick -> {ticker: impairment}
    trajectory: list[dict[str, float]]
    peak_impairment: dict[str, float]
    time_to_peak_days: dict[str, float]
    # First day impairment crosses `onset_threshold`. This, not time-to-peak,
    # is the operationally useful number: it answers "when does this reach my
    # holding?", whereas peaks cluster near the end of a long horizon and say
    # little about propagation order.
    onset_days: dict[str, float]
    nodes_affected: int
    contagion_multiplier: float
    onset_threshold: float = 0.05
    warnings: list[str] = field(default_factory=list)

    def at_tick(self, tick: int) -> dict[str, float]:
        return self.trajectory[max(0, min(tick, len(self.trajectory) - 1))]

    def top_affected(self, n: int = 10) -> list[tuple[str, float]]:
        return sorted(self.peak_impairment.items(), key=lambda kv: -kv[1])[:n]

    def cascade_order(self) -> list[tuple[str, float]]:
        """Nodes in the order the shock actually reaches them.

        This is the sequence a time-stepped visualisation should animate.
        """
        hit = [(t, d) for t, d in self.onset_days.items() if d == d]  # drop NaN
        return sorted(hit, key=lambda kv: kv[1])

    def second_order_only(self) -> dict[str, float]:
        """Nodes hit purely through the network, excluding the seeds.

        This is the number institutional factor-stress models miss, and the
        reason for building the graph at all.
        """
        return {
            k: v
            for k, v in self.peak_impairment.items()
            if k not in self.scenario.seeds and v > 1e-6
        }


class SupplyChainGraph:
    """Directed supplier -> customer graph with shock propagation."""

    def __init__(self) -> None:
        self.g = nx.DiGraph()

    # ------------------------------------------------------------- building

    def add_company(self, node: CompanyNode) -> None:
        self.g.add_node(node.ticker, data=node)

    def add_edge(self, edge: SupplyEdge) -> None:
        """Add a dependency, keeping the better-evidenced edge on conflict."""
        for t in (edge.source, edge.target):
            if t not in self.g:
                self.g.add_node(t, data=CompanyNode(ticker=t))
        existing = self.g.get_edge_data(edge.source, edge.target)
        if existing is not None:
            prev: SupplyEdge = existing["data"]
            # An observed edge always beats an inferred one, regardless of
            # confidence: provenance is not a score to be outbid.
            keep_new = (
                edge.provenance.is_observed and not prev.provenance.is_observed
            ) or (
                edge.provenance.is_observed == prev.provenance.is_observed
                and edge.confidence > prev.confidence
            )
            if not keep_new:
                return
        self.g.add_edge(edge.source, edge.target, data=edge)

    def node(self, ticker: str) -> CompanyNode:
        if ticker not in self.g:
            raise KeyError(f"{ticker} non presente nel grafo")
        return self.g.nodes[ticker]["data"]

    def edges(
        self, *, provenance: EdgeProvenance | None = None
    ) -> list[SupplyEdge]:
        out = [d["data"] for _, _, d in self.g.edges(data=True)]
        if provenance is not None:
            out = [e for e in out if e.provenance is provenance]
        return out

    @property
    def observed_share(self) -> float:
        """Fraction of edges actually disclosed in a filing.

        Report this alongside any result. A graph that is 80% inferred
        supports much weaker claims than one that is 80% observed.
        """
        all_edges = self.edges()
        if not all_edges:
            return 0.0
        return sum(1 for e in all_edges if e.provenance.is_observed) / len(all_edges)

    # -------------------------------------------------------------- analysis

    def systemic_importance(self, *, weight_by_dependence: bool = True) -> dict[str, float]:
        """Rank nodes by how much of the network depends on them.

        Uses Katz centrality on the reversed graph: a node scores highly when
        many others depend on it, directly or transitively. Falls back to
        weighted out-degree when Katz does not converge.
        """
        if self.g.number_of_nodes() == 0:
            return {}
        rev = self.g.reverse(copy=True)
        if weight_by_dependence:
            for u, v, d in rev.edges(data=True):
                rev[u][v]["w"] = d["data"].dependence
            wkey = "w"
        else:
            wkey = None
        try:
            # alpha must sit below 1/lambda_max for the series to converge.
            return nx.katz_centrality(
                rev, alpha=0.05, beta=1.0, max_iter=2000, tol=1e-8, weight=wkey
            )
        except (nx.PowerIterationFailedConvergence, nx.NetworkXError) as exc:
            log.info("Katz non convergente (%s): uso out-degree pesato.", exc)
            total = defaultdict(float)
            for e in self.edges():
                total[e.source] += e.dependence
            return dict(total)

    def single_points_of_failure(
        self, *, min_dependents: int = 3, min_dependence: float = 0.25
    ) -> list[tuple[str, int, float]]:
        """Suppliers that many companies depend on heavily.

        Returns (ticker, dependent_count, mean_dependence), worst first.
        """
        rows = []
        for ticker in self.g.nodes:
            deps = [
                self.g[ticker][v]["data"].dependence
                for v in self.g.successors(ticker)
            ]
            heavy = [d for d in deps if d >= min_dependence]
            if len(heavy) >= min_dependents:
                rows.append((ticker, len(heavy), sum(heavy) / len(heavy)))
        return sorted(rows, key=lambda r: (-r[1], -r[2]))

    def country_exposure(self) -> dict[str, float]:
        """Aggregate revenue-weighted exposure by country.

        Note this aggregates *revenue geography*, not domicile — the whole
        point of the distinction. Nodes with no segment data fall back to
        domicile and are effectively single-country, which understates real
        dispersion; `SupplyChainGraph.coverage()` reports how many.
        """
        agg: dict[str, float] = defaultdict(float)
        for ticker in self.g.nodes:
            node = self.node(ticker)
            weight = node.market_cap or 1.0
            geo = node.revenue_geography or (
                {node.country: 1.0} if node.country else {}
            )
            for country, share in geo.items():
                agg[country] += weight * share
        total = sum(agg.values())
        return {k: v / total for k, v in sorted(agg.items(), key=lambda kv: -kv[1])} if total else {}

    def coverage(self) -> dict[str, float | int]:
        """Data-quality summary. Always show this next to a result."""
        n = self.g.number_of_nodes()
        with_geo = sum(
            1 for t in self.g.nodes if self.node(t).revenue_geography
        )
        return {
            "nodes": n,
            "edges": self.g.number_of_edges(),
            "observed_edge_share": round(self.observed_share, 4),
            "nodes_with_revenue_geography": with_geo,
            "revenue_geography_coverage": round(with_geo / n, 4) if n else 0.0,
            "isolated_nodes": sum(1 for t in self.g.nodes if self.g.degree(t) == 0),
            "mean_in_degree": round(
                sum(d for _, d in self.g.in_degree()) / n, 3
            ) if n else 0.0,
        }

    # ----------------------------------------------------------- propagation

    def propagation_step(
        self,
        impairment: dict[str, float],
        buffer_left: dict[str, float],
        *,
        transmission: float,
        recovery_per_tick: float,
        tick_days: int,
        clamped: dict[str, float] | None = None,
        dead: set[str] | None = None,
    ) -> dict[str, float]:
        """Advance impairment by one tick. Mutates `buffer_left` in place.

        This is the two-clock physics, factored out so the scenario runner
        (`propagate_operational`) and the stochastic forward simulation share
        one implementation rather than drifting apart.

        `clamped` pins nodes at an imposed floor while a shock is active.
        `dead` marks bankrupt nodes, which stop transmitting: a liquidated
        supplier delivers nothing, so its customers are cut off rather than
        merely impaired.
        """
        clamped = clamped or {}
        dead = dead or set()

        pressure: dict[str, float] = {}
        for ticker in self.g.nodes:
            node = self.node(ticker)
            raw = 0.0
            for supplier in self.g.predecessors(ticker):
                edge: SupplyEdge = self.g[supplier][ticker]["data"]
                # A bankrupt supplier supplies nothing: full dependence lost.
                upstream = 1.0 if supplier in dead else impairment.get(supplier, 0.0)
                raw += upstream * edge.dependence
            pressure[ticker] = min(
                1.0, raw * transmission * (1.0 - node.substitutability)
            )

        nxt: dict[str, float] = {}
        for ticker in self.g.nodes:
            if ticker in dead:
                nxt[ticker] = 1.0
                continue
            p = pressure[ticker]
            current = impairment.get(ticker, 0.0)

            if ticker in clamped:
                nxt[ticker] = max(clamped[ticker], p)
                continue

            if p > 1e-9 and buffer_left.get(ticker, 0.0) > 0:
                # Inventory absorbs this tick, depleting in proportion to how
                # hard the pressure is.
                buffer_left[ticker] = max(
                    0.0, buffer_left[ticker] - tick_days * p
                )
                nxt[ticker] = max(0.0, current - recovery_per_tick)
                continue

            target = max(p, 0.0)
            moved = current + 0.5 * (target - current)
            nxt[ticker] = max(0.0, min(1.0, moved - recovery_per_tick))
        return nxt

    def propagate_operational(self, scenario: ShockScenario) -> PropagationResult:
        """Slow channel: physical disruption through inventory buffers.

        Per tick, for each node:
          pressure = sum over suppliers of  impairment(s) * dependence(s,node)
          pressure *= transmission * (1 - substitutability)
          the node's inventory absorbs pressure until depleted, after which
          impairment converges toward pressure; recovery pulls it back down.

        The buffer is what produces the realistic shape: nothing happens for
        weeks, then output falls sharply. Removing it yields a same-day
        cascade, which is the tell of a model that has not thought about
        physical time.
        """
        warnings: list[str] = []
        missing = [t for t in scenario.seeds if t not in self.g]
        if missing:
            warnings.append(
                f"Seed non presenti nel grafo, ignorati: {', '.join(sorted(missing))}"
            )
        live_seeds = {k: v for k, v in scenario.seeds.items() if k in self.g}
        if not live_seeds:
            raise ValueError(
                "nessun seed dello scenario e' presente nel grafo: "
                f"richiesti {sorted(scenario.seeds)}"
            )
        if self.observed_share < 0.5:
            warnings.append(
                f"Solo il {self.observed_share:.0%} degli archi e' osservato in "
                "filing: i risultati di secondo ordine sono indicativi."
            )

        impairment = {t: 0.0 for t in self.g.nodes}
        impairment.update(live_seeds)
        # Remaining inventory buffer, in days, per node.
        buffer_left = {t: self.node(t).inventory_days for t in self.g.nodes}

        onset_threshold = 0.05
        trajectory: list[dict[str, float]] = [dict(impairment)]
        peak = dict(impairment)
        peak_tick = {t: (0 if impairment[t] > 0 else -1) for t in self.g.nodes}
        onset_tick: dict[str, int] = {
            t: (0 if impairment[t] >= onset_threshold else -1) for t in self.g.nodes
        }

        for tick in range(1, scenario.n_ticks + 1):
            impairment = self.propagation_step(
                impairment, buffer_left,
                transmission=scenario.transmission,
                recovery_per_tick=scenario.recovery_per_tick,
                tick_days=scenario.tick_days,
                clamped=live_seeds,
            )
            trajectory.append(dict(impairment))
            for ticker, val in impairment.items():
                if val > peak[ticker] + 1e-12:
                    peak[ticker] = val
                    peak_tick[ticker] = tick
                if onset_tick[ticker] < 0 and val >= onset_threshold:
                    onset_tick[ticker] = tick

        seed_mass = sum(live_seeds.values())
        total_mass = sum(peak.values())
        return PropagationResult(
            scenario=scenario,
            trajectory=trajectory,
            peak_impairment=peak,
            time_to_peak_days={
                t: (peak_tick[t] * scenario.tick_days if peak_tick[t] >= 0 else float("nan"))
                for t in peak
            },
            onset_days={
                t: (onset_tick[t] * scenario.tick_days if onset_tick[t] >= 0 else float("nan"))
                for t in peak
            },
            nodes_affected=sum(1 for v in peak.values() if v > 1e-6),
            contagion_multiplier=(total_mass / seed_mass) if seed_mass > 0 else 0.0,
            onset_threshold=onset_threshold,
            warnings=warnings,
        )

    def propagate_market(
        self, scenario: ShockScenario, *, impairment_to_return: float = -0.8
    ) -> dict[str, float]:
        """Fast channel: same-day repricing.

        Two additive components:
          * a beta-scaled index move (`market_beta_shock`)
          * an idiosyncratic hit proportional to *peak* operational
            impairment, since equity prices discount the expected
            disruption immediately rather than waiting for inventories to
            run out.

        Returning a dict of expected returns keeps this composable with the
        portfolio stress test, which is where the number is actually used.
        """
        op = self.propagate_operational(scenario)
        out: dict[str, float] = {}
        for ticker in self.g.nodes:
            node = self.node(ticker)
            systematic = node.beta * scenario.market_beta_shock
            idiosyncratic = op.peak_impairment[ticker] * impairment_to_return
            out[ticker] = systematic + idiosyncratic
        return out

    def stress_portfolio(
        self, weights: dict[str, float], scenario: ShockScenario, **kw
    ) -> dict[str, float]:
        """Expected portfolio return under `scenario`.

        Names not in the graph contribute the pure systematic term, so an
        incomplete graph understates rather than silently drops exposure.
        """
        shocks = self.propagate_market(scenario, **kw)
        total = sum(weights.values())
        if total <= 0:
            raise ValueError("i pesi di portafoglio sommano a zero")

        contributions: dict[str, float] = {}
        uncovered = 0.0
        for ticker, w in weights.items():
            if ticker in shocks:
                contributions[ticker] = (w / total) * shocks[ticker]
            else:
                uncovered += w / total
                contributions[ticker] = (w / total) * scenario.market_beta_shock

        return {
            "portfolio_return": sum(contributions.values()),
            "uncovered_weight": uncovered,
            "worst_contributor": (
                min(contributions.items(), key=lambda kv: kv[1])[0]
                if contributions else ""
            ),
            **{f"contrib_{k}": v for k, v in contributions.items()},
        }

    # ----------------------------------------------------------- export

    def to_pyvis_html(self, path: str, *, height: str = "800px") -> str | None:
        """Render an interactive graph. Returns the path, or None without PyVis.

        Observed edges are drawn solid, inferred ones dashed, so provenance
        survives into the picture instead of being flattened out of it.
        """
        try:
            from pyvis.network import Network  # type: ignore
        except ImportError:
            log.info("PyVis non installato: export HTML salta.")
            return None

        net = Network(height=height, width="100%", directed=True,
                      bgcolor="#111318", font_color="#e8e8ea")
        importance = self.systemic_importance()
        max_imp = max(importance.values()) if importance else 1.0

        for ticker in self.g.nodes:
            node = self.node(ticker)
            imp = importance.get(ticker, 0.0) / max_imp if max_imp else 0.0
            net.add_node(
                ticker,
                label=ticker,
                title=(
                    f"{node.name or ticker}\nSettore: {node.sector or 'n/d'}\n"
                    f"Paese (ricavi): {node.primary_country or 'n/d'}\n"
                    f"Importanza sistemica: {imp:.2f}\n"
                    f"Scorte: {node.inventory_days:.0f} gg"
                ),
                value=10 + 40 * imp,
                color="#e06c4f" if imp > 0.6 else "#4f8fe0",
            )

        for edge in self.edges():
            net.add_edge(
                edge.source, edge.target,
                value=max(1.0, edge.dependence * 10),
                title=(
                    f"{edge.source} -> {edge.target}\n"
                    f"Dipendenza: {edge.dependence:.1%}\n"
                    f"Provenienza: {edge.provenance.value}\n"
                    f"Confidenza: {edge.confidence:.2f}"
                ),
                dashes=not edge.provenance.is_observed,
                color="#6ba368" if edge.provenance.is_observed else "#8a8a94",
            )

        net.write_html(path, notebook=False)
        return path

    def to_json(self) -> dict:
        """Compact serialisation, for the Worker to serve to a browser renderer."""
        return {
            "nodes": [
                {
                    "id": t,
                    "name": self.node(t).name,
                    "sector": self.node(t).sector,
                    "country": self.node(t).primary_country,
                    "revenue_geography": self.node(t).revenue_geography,
                    "inventory_days": self.node(t).inventory_days,
                    "substitutability": self.node(t).substitutability,
                    "beta": self.node(t).beta,
                }
                for t in self.g.nodes
            ],
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "dependence": e.dependence,
                    "provenance": e.provenance.value,
                    "confidence": e.confidence,
                    "observed": e.provenance.is_observed,
                }
                for e in self.edges()
            ],
            "coverage": self.coverage(),
        }

    @classmethod
    def from_edges(
        cls, edges: Iterable[SupplyEdge], nodes: Iterable[CompanyNode] = ()
    ) -> "SupplyChainGraph":
        g = cls()
        for n in nodes:
            g.add_company(n)
        for e in edges:
            g.add_edge(e)
        return g
