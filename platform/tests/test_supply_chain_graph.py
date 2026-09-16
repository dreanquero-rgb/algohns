"""Tests for relation extraction, graph assembly and shock propagation."""
from __future__ import annotations

import pytest

from modules.supply_chain_graph import (
    CompanyNode,
    EdgeProvenance,
    PropagationResult,
    Relation,
    ShockScenario,
    SupplyChainGraph,
    SupplyEdge,
    extract_relations,
)

KNOWN = {
    "apple": "AAPL", "dell technologies": "DELL",
    "taiwan semiconductor manufacturing": "TSM", "asml": "ASML",
    "applied materials": "AMAT", "jabil": "JBL",
}


class TestExtraction:
    def test_finds_customers_after_cue(self):
        txt = "Our largest customers include Apple Inc. and Dell Technologies."
        got = {n for n, r, _, _ in extract_relations(txt, known_companies=KNOWN)
               if r is Relation.CUSTOMER_OF}
        assert {"AAPL", "DELL"} <= got

    def test_abbreviation_does_not_split_sentence(self):
        """"Apple Inc. and Dell" must not lose Dell to a sentence break."""
        txt = "Our principal customers include Apple Inc. and Dell Technologies."
        names = {n for n, _, _, _ in extract_relations(txt, known_companies=KNOWN)}
        assert "DELL" in names

    def test_finds_suppliers(self):
        txt = ("We purchase substantially all of our wafers from Taiwan "
               "Semiconductor Manufacturing Company.")
        got = {n for n, r, _, _ in extract_relations(txt, known_companies=KNOWN)
               if r is Relation.SUPPLIES}
        assert "TSM" in got

    def test_candidate_trimmed_at_legal_suffix(self):
        """An entity must not run past a sentence boundary into the next one."""
        txt = ("We rely heavily upon a limited number of suppliers including "
               "Applied Materials Inc. The United States economy was strong.")
        names = {n for n, _, _, _ in extract_relations(txt, known_companies=KNOWN)}
        assert "AMAT" in names
        assert not any("United" in n for n in names)

    def test_two_letter_suffix_does_not_truncate_longer_word(self):
        """'se' must not fire inside 'Semiconductor'."""
        txt = ("We purchase substantially all of our logic wafers from "
               "Taiwan Semiconductor Manufacturing Company.")
        names = {n for n, _, _, _ in extract_relations(txt, known_companies=KNOWN)}
        assert "TSM" in names
        assert not any(n.endswith("Se") for n in names)

    def test_no_relations_without_a_cue(self):
        txt = "Apple Inc. and Dell Technologies are large technology companies."
        assert extract_relations(txt, known_companies=KNOWN) == []

    def test_boilerplate_yields_nothing(self):
        noise = ("Item 1A. Risk Factors. The United States and China remained "
                 "key markets. See Note 12. Management Discussion follows.")
        assert extract_relations(noise, known_companies=KNOWN) == []

    def test_known_company_scores_higher_than_unknown(self):
        txt = "Our principal suppliers are Jabil Inc. and Obscureco Systems."
        rows = {n: c for n, _, c, _ in extract_relations(txt, known_companies=KNOWN)}
        assert rows["JBL"] > max(
            (v for k, v in rows.items() if k != "JBL"), default=0.0
        )

    def test_percentage_raises_confidence(self):
        plain = "Our largest customers include Apple Inc."
        with_pct = "Our largest customers include Apple Inc. accounted for 22%."
        c1 = dict((n, c) for n, _, c, _ in extract_relations(plain, known_companies=KNOWN))
        c2 = dict((n, c) for n, _, c, _ in extract_relations(with_pct, known_companies=KNOWN))
        assert c2["AAPL"] >= c1["AAPL"]

    def test_min_confidence_filters(self):
        txt = "Our principal suppliers are Obscureco Systems."
        assert extract_relations(txt, min_confidence=0.9) == []

    def test_evidence_is_captured_for_audit(self):
        txt = "Our largest customers include Apple Inc."
        _, _, _, evidence = extract_relations(txt, known_companies=KNOWN)[0]
        assert "customers" in evidence.lower()

    def test_html_is_stripped(self):
        txt = "<p>Our largest customers include <b>Apple Inc.</b></p>"
        names = {n for n, _, _, _ in extract_relations(txt, known_companies=KNOWN)}
        assert "AAPL" in names


class TestValidation:
    def test_dependence_range_enforced(self):
        with pytest.raises(ValueError, match="dependence"):
            SupplyEdge("A", "B", EdgeProvenance.MANUAL, dependence=1.5)

    def test_confidence_range_enforced(self):
        with pytest.raises(ValueError, match="confidence"):
            SupplyEdge("A", "B", EdgeProvenance.MANUAL, confidence=-0.1)

    def test_self_loop_rejected(self):
        with pytest.raises(ValueError, match="self-loop"):
            SupplyEdge("A", "A", EdgeProvenance.MANUAL)

    def test_substitutability_range_enforced(self):
        with pytest.raises(ValueError, match="substitutability"):
            CompanyNode("A", substitutability=2.0)

    def test_negative_inventory_rejected(self):
        with pytest.raises(ValueError, match="inventory_days"):
            CompanyNode("A", inventory_days=-5)

    def test_scenario_needs_seeds(self):
        with pytest.raises(ValueError, match="almeno un seed"):
            ShockScenario("empty", seeds={})

    def test_scenario_seed_range_enforced(self):
        with pytest.raises(ValueError, match="fuori range"):
            ShockScenario("bad", seeds={"A": 1.5})

    def test_horizon_must_cover_a_tick(self):
        with pytest.raises(ValueError, match="horizon_days"):
            ShockScenario("short", seeds={"A": 1.0}, horizon_days=3, tick_days=7)


@pytest.fixture
def chain() -> SupplyChainGraph:
    nodes = [
        CompanyNode("TSM", "TSMC", "Semis", "TW",
                    revenue_geography={"TW": 0.2, "US": 0.6, "CN": 0.2},
                    market_cap=5e11, inventory_days=0.0, substitutability=0.05),
        CompanyNode("NVDA", "NVIDIA", "Semis", "US", market_cap=3e12,
                    inventory_days=60, substitutability=0.10, beta=1.7),
        CompanyNode("AMD", "AMD", "Semis", "US", market_cap=3e11,
                    inventory_days=50, substitutability=0.15),
        CompanyNode("DELL", "Dell", "Hardware", "US", market_cap=8e10,
                    inventory_days=25, substitutability=0.35),
        CompanyNode("MSFT", "Microsoft", "Software", "US", market_cap=3e12,
                    inventory_days=120, substitutability=0.60),
    ]
    edges = [
        SupplyEdge("TSM", "NVDA", EdgeProvenance.OBSERVED_FILING, dependence=0.90),
        SupplyEdge("TSM", "AMD", EdgeProvenance.OBSERVED_FILING, dependence=0.85),
        SupplyEdge("NVDA", "DELL", EdgeProvenance.OBSERVED_FILING, dependence=0.55),
        SupplyEdge("AMD", "DELL", EdgeProvenance.INFERRED_IO, dependence=0.30),
        SupplyEdge("NVDA", "MSFT", EdgeProvenance.OBSERVED_FILING, dependence=0.40),
    ]
    return SupplyChainGraph.from_edges(edges, nodes)


class TestGraph:
    def test_nodes_and_edges_registered(self, chain):
        assert chain.g.number_of_nodes() == 5
        assert chain.g.number_of_edges() == 5

    def test_observed_share(self, chain):
        assert chain.observed_share == pytest.approx(4 / 5)

    def test_observed_edge_beats_inferred_regardless_of_confidence(self):
        """Provenance is not a score that a confident guess can outbid."""
        g = SupplyChainGraph()
        g.add_edge(SupplyEdge("A", "B", EdgeProvenance.INFERRED_IO, confidence=0.99))
        g.add_edge(SupplyEdge("A", "B", EdgeProvenance.OBSERVED_FILING, confidence=0.20))
        assert chain_edge(g, "A", "B").provenance is EdgeProvenance.OBSERVED_FILING

    def test_higher_confidence_wins_within_same_provenance(self):
        g = SupplyChainGraph()
        g.add_edge(SupplyEdge("A", "B", EdgeProvenance.OBSERVED_FILING,
                              confidence=0.4, dependence=0.1))
        g.add_edge(SupplyEdge("A", "B", EdgeProvenance.OBSERVED_FILING,
                              confidence=0.8, dependence=0.6))
        assert chain_edge(g, "A", "B").dependence == pytest.approx(0.6)

    def test_inferred_does_not_overwrite_observed(self):
        g = SupplyChainGraph()
        g.add_edge(SupplyEdge("A", "B", EdgeProvenance.OBSERVED_FILING, confidence=0.5))
        g.add_edge(SupplyEdge("A", "B", EdgeProvenance.INFERRED_IO, confidence=0.99))
        assert chain_edge(g, "A", "B").provenance is EdgeProvenance.OBSERVED_FILING

    def test_tsm_is_most_systemically_important(self, chain):
        ranking = chain.systemic_importance()
        assert max(ranking.items(), key=lambda kv: kv[1])[0] == "TSM"

    def test_single_point_of_failure_detected(self, chain):
        spofs = chain.single_points_of_failure(min_dependents=2, min_dependence=0.25)
        assert spofs and spofs[0][0] == "TSM"

    def test_revenue_geography_beats_domicile(self, chain):
        """TSM's primary country is US by revenue, not TW by domicile."""
        assert chain.node("TSM").country == "TW"
        assert chain.node("TSM").primary_country == "US"

    def test_country_exposure_sums_to_one(self, chain):
        exp = chain.country_exposure()
        assert sum(exp.values()) == pytest.approx(1.0)

    def test_coverage_reports_provenance_mix(self, chain):
        cov = chain.coverage()
        assert cov["nodes"] == 5
        assert cov["observed_edge_share"] == pytest.approx(0.8)

    def test_unknown_node_raises(self, chain):
        with pytest.raises(KeyError, match="NOPE"):
            chain.node("NOPE")

    def test_to_json_roundtrips_shape(self, chain):
        payload = chain.to_json()
        assert len(payload["nodes"]) == 5
        assert len(payload["edges"]) == 5
        assert all("observed" in e for e in payload["edges"])


def chain_edge(g: SupplyChainGraph, u: str, v: str) -> SupplyEdge:
    return g.g[u][v]["data"]


class TestPropagation:
    def _scenario(self, **kw) -> ShockScenario:
        base = dict(name="taiwan", seeds={"TSM": 1.0}, horizon_days=728,
                    tick_days=7, recovery_per_tick=0.02)
        base.update(kw)
        return ShockScenario(**base)

    def test_inventory_delays_onset(self, chain):
        """The buffer is the whole point: no same-day cascade."""
        res = chain.propagate_operational(self._scenario())
        # NVDA holds 60 days of inventory, so weeks 1-4 must show nothing.
        for wk in range(1, 5):
            assert res.at_tick(wk)["NVDA"] == pytest.approx(0.0, abs=1e-9)
        # But it does break through eventually.
        assert res.peak_impairment["NVDA"] > 0.1

    def test_shorter_buffer_breaks_first(self, chain):
        """Onset order, not peak order: peaks cluster at the horizon end."""
        res = chain.propagate_operational(self._scenario())
        # AMD holds 50 days of inventory, NVDA 60, so AMD breaks first.
        assert res.onset_days["AMD"] < res.onset_days["NVDA"]

    def test_cascade_order_follows_the_graph(self, chain):
        """The seed is hit first, then tier 2, then tier 3."""
        res = chain.propagate_operational(self._scenario())
        order = [t for t, _ in res.cascade_order()]
        assert order[0] == "TSM"
        assert order.index("DELL") > order.index("NVDA")

    def test_unaffected_node_has_no_onset(self, chain):
        res = chain.propagate_operational(self._scenario())
        assert res.onset_days["MSFT"] != res.onset_days["MSFT"]  # NaN

    def test_third_tier_is_reached(self, chain):
        """DELL depends on NVDA/AMD, not on TSM directly."""
        res = chain.propagate_operational(self._scenario())
        assert res.peak_impairment["DELL"] > 0.01

    def test_high_substitutability_absorbs_shock(self, chain):
        """MSFT re-sources easily and holds 120 days: it should barely move."""
        res = chain.propagate_operational(self._scenario())
        assert res.peak_impairment["MSFT"] < 0.05

    def test_second_order_excludes_seeds(self, chain):
        res = chain.propagate_operational(self._scenario())
        assert "TSM" not in res.second_order_only()
        assert "NVDA" in res.second_order_only()

    def test_contagion_multiplier_exceeds_one(self, chain):
        res = chain.propagate_operational(self._scenario())
        assert res.contagion_multiplier > 1.0

    def test_impairment_stays_in_unit_interval(self, chain):
        res = chain.propagate_operational(self._scenario())
        for snapshot in res.trajectory:
            assert all(0.0 <= v <= 1.0 for v in snapshot.values())

    @pytest.mark.parametrize("mag", [0.2, 0.5, 0.8, 1.0])
    def test_total_impact_monotone_in_seed(self, chain, mag):
        weaker = chain.propagate_operational(
            self._scenario(seeds={"TSM": max(mag - 0.2, 0.05)})
        )
        stronger = chain.propagate_operational(self._scenario(seeds={"TSM": mag}))
        assert sum(stronger.peak_impairment.values()) >= \
               sum(weaker.peak_impairment.values()) - 1e-9

    def test_zero_transmission_isolates_seed(self, chain):
        res = chain.propagate_operational(self._scenario(transmission=0.0))
        assert res.second_order_only() == {}

    def test_missing_seed_warns_but_runs(self, chain):
        res = chain.propagate_operational(
            self._scenario(seeds={"TSM": 1.0, "GHOST": 0.5})
        )
        assert any("GHOST" in w for w in res.warnings)

    def test_all_seeds_missing_raises(self, chain):
        with pytest.raises(ValueError, match="nessun seed"):
            chain.propagate_operational(self._scenario(seeds={"GHOST": 1.0}))

    def test_trajectory_length_matches_ticks(self, chain):
        sc = self._scenario(horizon_days=70, tick_days=7)
        res = chain.propagate_operational(sc)
        assert len(res.trajectory) == sc.n_ticks + 1


class TestMarketChannel:
    def test_beta_scales_systematic_shock(self, chain):
        sc = ShockScenario("beta only", seeds={"TSM": 0.0001},
                           horizon_days=7, tick_days=7, market_beta_shock=-0.10)
        shocks = chain.propagate_market(sc)
        # NVDA has beta 1.7, MSFT 1.0; the systematic term must scale.
        assert shocks["NVDA"] < shocks["MSFT"]

    def test_operational_damage_adds_idiosyncratic_loss(self, chain):
        sc = ShockScenario("full", seeds={"TSM": 1.0}, horizon_days=728,
                           tick_days=7, recovery_per_tick=0.02)
        shocks = chain.propagate_market(sc)
        assert shocks["NVDA"] < 0  # impaired names lose value

    def test_portfolio_stress_flags_uncovered_weight(self, chain):
        sc = ShockScenario("s", seeds={"TSM": 1.0}, horizon_days=364, tick_days=7)
        out = chain.stress_portfolio({"NVDA": 0.5, "KO": 0.5}, sc)
        assert out["uncovered_weight"] == pytest.approx(0.5)

    def test_portfolio_stress_identifies_worst_name(self, chain):
        sc = ShockScenario("s", seeds={"TSM": 1.0}, horizon_days=728,
                           tick_days=7, recovery_per_tick=0.02)
        out = chain.stress_portfolio({"NVDA": 0.5, "MSFT": 0.5}, sc)
        assert out["worst_contributor"] == "NVDA"

    def test_zero_weights_rejected(self, chain):
        sc = ShockScenario("s", seeds={"TSM": 1.0}, horizon_days=364, tick_days=7)
        with pytest.raises(ValueError, match="sommano a zero"):
            chain.stress_portfolio({"NVDA": 0.0}, sc)
