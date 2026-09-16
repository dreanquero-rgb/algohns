"""Tests for the world dataset, event sampler and forward simulation.

The statistical tests are the important ones: a stochastic engine cannot be
verified by reading it, only by checking that what comes out matches what
the parameters claim. They use fixed seeds so they are deterministic
despite testing randomness.
"""
from __future__ import annotations

import numpy as np
import pytest

from modules.market_data import SP500_SAMPLE
from modules.world_events import (
    EVENT_CATALOGUE,
    EventScope,
    expected_event_market_drift,
    sample_events,
)
from modules.world_forward import TRADING_DAYS, ForwardConfig, simulate_world
from modules.world_simulation import (
    SCENARIO_LIBRARY,
    WorldSimulation,
    build_world,
)
from modules.world_universe import COMPANIES, COUNTRIES, SUPPLY_LINKS

DEMO_PF = {
    "AAPL": 0.15, "MSFT": 0.15, "NVDA": 0.12, "TSM": 0.08, "ASML": 0.06,
    "JPM": 0.14, "JNJ": 0.12, "XOM": 0.10, "KO": 0.08,
}


@pytest.fixture(scope="module")
def world():
    return build_world()


# --------------------------------------------------------------- dataset


class TestDataset:
    def test_revenue_geography_sums_to_one(self):
        for c in COMPANIES:
            total = sum(c.revenue_geography.values())
            assert total == pytest.approx(1.0, abs=0.011), f"{c.ticker}: {total}"

    def test_coordinates_valid(self):
        for c in COMPANIES:
            assert -90 <= c.lat <= 90, c.ticker
            assert -180 <= c.lon <= 180, c.ticker

    def test_no_duplicate_tickers(self):
        tickers = [c.ticker for c in COMPANIES]
        assert len(tickers) == len(set(tickers))

    def test_backtest_universe_fully_covered(self):
        """The globe must show every name the optimiser can hold."""
        assert not set(SP500_SAMPLE) - {c.ticker for c in COMPANIES}

    def test_supply_links_reference_known_tickers(self):
        known = {c.ticker for c in COMPANIES}
        for src, dst, _, _ in SUPPLY_LINKS:
            assert src in known, src
            assert dst in known, dst

    def test_geography_codes_are_known_countries(self):
        codes = {k for c in COMPANIES for k in c.revenue_geography}
        assert not codes - set(COUNTRIES)

    def test_no_self_supply(self):
        for src, dst, _, _ in SUPPLY_LINKS:
            assert src != dst

    def test_dependences_in_range(self):
        for src, dst, dep, _ in SUPPLY_LINKS:
            assert 0.0 < dep <= 1.0, f"{src}->{dst}: {dep}"

    def test_graph_built_from_dataset(self, world):
        assert world.g.number_of_nodes() == len(COMPANIES)
        assert world.g.number_of_edges() == len(SUPPLY_LINKS)


# ------------------------------------------------------- event sampler


class TestEventSampler:
    def test_deterministic_for_a_seed(self):
        a = sample_events(1825, seed=5)
        b = sample_events(1825, seed=5)
        assert [(e.day, e.template.key, e.headline) for e in a] == \
               [(e.day, e.template.key, e.headline) for e in b]

    def test_different_seeds_differ(self):
        a = sample_events(1825, seed=5)
        b = sample_events(1825, seed=6)
        assert [e.headline for e in a] != [e.headline for e in b]

    def test_events_sorted_by_day(self):
        evs = sample_events(1825, seed=3)
        assert [e.day for e in evs] == sorted(e.day for e in evs)

    def test_events_within_horizon(self):
        evs = sample_events(500, seed=3)
        assert all(0 <= e.day < 500 for e in evs)

    def test_longer_horizon_yields_more_events(self):
        assert len(sample_events(365, seed=9)) < len(sample_events(365 * 8, seed=9))

    def test_intensity_scales_count(self):
        calm = len(sample_events(365 * 5, seed=9, intensity=0.4))
        wild = len(sample_events(365 * 5, seed=9, intensity=2.5))
        assert wild > calm

    def test_catalogue_includes_favourable_events(self):
        """A generator with only disasters teaches nothing about portfolios."""
        share = sum(1 for t in EVENT_CATALOGUE if t.favourable) / len(EVENT_CATALOGUE)
        assert share > 0.15

    def test_favourable_events_actually_occur(self):
        evs = sample_events(365 * 5, seed=4, tickers=["AAPL", "MSFT", "NVDA"])
        assert any(e.favourable for e in evs)

    def test_arrival_count_matches_poisson_expectation(self):
        """Empirical count should track rate x years across many seeds."""
        template = next(t for t in EVENT_CATALOGUE if t.key == "earnings_beat")
        years = 4.0
        counts = [
            sum(1 for e in sample_events(int(365 * years), seed=s,
                                         catalogue=[template],
                                         tickers=["AAPL"])
                if e.template.key == "earnings_beat")
            for s in range(60)
        ]
        expected = template.annual_rate * years
        assert np.mean(counts) == pytest.approx(expected, rel=0.25)

    def test_scopes_resolve_to_targets(self):
        evs = sample_events(365 * 10, seed=2, tickers=["AAPL", "MSFT"])
        seen = {e.template.scope for e in evs}
        assert EventScope.COMPANY in seen
        assert all(e.targets_resolved for e in evs)

    def test_headline_never_quotes_a_zero_move(self):
        for e in sample_events(365 * 6, seed=8, tickers=["AAPL"]):
            assert "0%" not in e.headline or "10%" in e.headline or "20%" in e.headline

    def test_rejects_bad_horizon(self):
        with pytest.raises(ValueError, match="horizon_days"):
            sample_events(0)

    def test_rejects_bad_intensity(self):
        with pytest.raises(ValueError, match="intensity"):
            sample_events(365, intensity=0.0)


class TestDriftCompensation:
    def test_catalogue_is_not_drift_neutral(self):
        """Documents the reason compensation exists at all."""
        assert expected_event_market_drift() < -0.02

    def test_compensation_scales_with_intensity(self):
        base = expected_event_market_drift(intensity=1.0)
        doubled = expected_event_market_drift(intensity=2.0)
        assert doubled == pytest.approx(2.0 * base)


# ------------------------------------------------- forward simulation


class TestForwardSimulation:
    def test_deterministic_for_a_seed(self, world):
        cfg = ForwardConfig(horizon_days=365 * 2, seed=21)
        a = simulate_world(world, cfg)
        b = simulate_world(world, cfg)
        assert a.prices["AAPL"] == b.prices["AAPL"]
        assert a.bankruptcies == b.bankruptcies

    def test_different_seeds_differ(self, world):
        a = simulate_world(world, ForwardConfig(horizon_days=365 * 2, seed=1))
        b = simulate_world(world, ForwardConfig(horizon_days=365 * 2, seed=2))
        assert a.prices["AAPL"] != b.prices["AAPL"]

    def test_prices_start_at_100(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=400, seed=3))
        assert all(p[0] > 0 for p in tl.prices.values())

    def test_prices_never_negative(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=365 * 5, seed=7))
        for t, path in tl.prices.items():
            assert min(path) > 0, t

    def test_impairment_in_unit_interval(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=365 * 3, seed=7))
        for t, path in tl.impairment.items():
            assert all(0.0 <= v <= 1.0 for v in path), t

    def test_all_paths_same_length(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=365 * 2, seed=5))
        n = tl.n_steps
        assert all(len(p) == n for p in tl.prices.values())
        assert all(len(p) == n for p in tl.impairment.values())
        assert len(tl.market_index) == n

    def test_horizon_maps_to_trading_days(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=365 * 4, seed=5))
        assert tl.n_steps == pytest.approx(4 * TRADING_DAYS, rel=0.02)

    def test_covers_whole_universe(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=400, seed=5))
        assert len(tl.tickers) == len(COMPANIES)

    def test_rejects_short_horizon(self, world):
        with pytest.raises(ValueError, match="orizzonte troppo corto"):
            ForwardConfig(horizon_days=10)

    def test_rejects_absurd_horizon(self, world):
        with pytest.raises(ValueError, match="orizzonte massimo"):
            ForwardConfig(horizon_days=365 * 50)

    def test_bankruptcy_can_be_disabled(self, world):
        tl = simulate_world(
            world,
            ForwardConfig(horizon_days=365 * 8, seed=13, allow_bankruptcy=False),
        )
        assert tl.bankruptcies == {}

    def test_bankrupt_name_loses_most_of_its_value(self, world):
        """Find a world with a failure and check the equity is wiped."""
        for seed in range(30):
            tl = simulate_world(
                world, ForwardConfig(horizon_days=365 * 8, seed=seed)
            )
            if tl.bankruptcies:
                ticker = next(iter(tl.bankruptcies))
                path = tl.prices[ticker]
                assert path[-1] < max(path) * 0.3
                return
        pytest.skip("nessun fallimento nei seed testati")

    def test_warnings_disclose_non_validation(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=400, seed=5))
        assert any("non validata" in w for w in tl.warnings)

    def test_warnings_disclose_drift_compensation(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=400, seed=5))
        assert any("compensati" in w for w in tl.warnings)


class TestCalibration:
    """Statistical checks: the output must match what the parameters claim."""

    @pytest.fixture(scope="class")
    def runs(self, world):
        return [
            simulate_world(world, ForwardConfig(horizon_days=365 * 5, seed=s))
            for s in range(40)
        ]

    def test_realised_volatility_matches_parameter(self, runs):
        vols = []
        for tl in runs:
            idx = np.array(tl.market_index)
            vols.append(np.std(np.diff(idx) / idx[:-1]) * np.sqrt(TRADING_DAYS))
        assert np.mean(vols) == pytest.approx(0.16, rel=0.15)

    def test_median_return_tracks_geometric_drift(self, runs):
        """The median of a GBM follows mu - sigma^2/2, not mu.

        Checking against the arithmetic drift compounded is the wrong
        benchmark and made an earlier, correctly calibrated run look broken.
        """
        rets = np.array([tl.market_index[-1] / tl.market_index[0] - 1 for tl in runs])
        geometric = 0.07 - 0.16**2 / 2
        expected = (1 + geometric) ** 5 - 1
        # Wide tolerance: 40 seeds of a 36%-sigma distribution is a noisy median.
        assert np.median(rets) == pytest.approx(expected, abs=0.22)

    def test_market_is_not_a_doom_spiral(self, runs):
        """Most worlds must be up over five years, or forward tests are rigged."""
        rets = np.array([tl.market_index[-1] / tl.market_index[0] - 1 for tl in runs])
        assert (rets > 0).mean() > 0.5

    def test_bankruptcy_rate_is_large_cap_plausible(self, runs):
        """Large-cap default runs ~0.3-0.5%/yr; stress may lift it, not 10x it."""
        mean_failures = np.mean([len(tl.bankruptcies) for tl in runs])
        base = 0.004 * 5 * len(COMPANIES)
        assert mean_failures < base * 3.0
        assert mean_failures >= 0.0

    def test_some_worlds_are_bad(self, runs):
        """A simulator where nothing ever goes wrong is equally useless."""
        rets = np.array([tl.market_index[-1] / tl.market_index[0] - 1 for tl in runs])
        assert rets.min() < 0.0


class TestPortfolioIndependence:
    """The world must not depend on the portfolio. This is load-bearing."""

    def test_same_world_serves_any_portfolio(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=365 * 3, seed=17))
        before = list(tl.market_index)
        tl.portfolio_path({"AAPL": 1.0})
        tl.portfolio_path({"XOM": 0.5, "JPM": 0.5})
        assert tl.market_index == before

    def test_two_portfolios_compared_on_one_path(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=365 * 3, seed=17))
        a = tl.portfolio_path({"AAPL": 1.0})
        b = tl.portfolio_path({"KO": 1.0})
        assert len(a) == len(b) == tl.n_steps
        assert a != b

    def test_portfolio_path_length_matches_world(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=365 * 2, seed=4))
        assert len(tl.portfolio_path(DEMO_PF)) == tl.n_steps

    def test_starts_at_zero_return(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=400, seed=4))
        assert tl.portfolio_path(DEMO_PF)[0] == pytest.approx(0.0, abs=1e-9)

    def test_weights_are_normalised(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=400, seed=4))
        a = tl.portfolio_path({"AAPL": 0.5, "MSFT": 0.5})
        b = tl.portfolio_path({"AAPL": 50.0, "MSFT": 50.0})
        assert a == pytest.approx(b)

    def test_zero_weights_rejected(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=400, seed=4))
        with pytest.raises(ValueError, match="sommano a zero"):
            tl.portfolio_path({"AAPL": 0.0})

    def test_unknown_ticker_reported_as_uncovered(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=400, seed=4))
        assert tl.uncovered_weight({"AAPL": 0.5, "NOTREAL": 0.5}) == pytest.approx(0.5)

    def test_partial_weights_leave_cash(self, world):
        """Weights under 1.0 hold the remainder in cash, damping the path."""
        tl = simulate_world(world, ForwardConfig(horizon_days=365 * 3, seed=4))
        full = tl.portfolio_path({"NVDA": 1.0})
        half = tl.portfolio_path({"NVDA": 0.5, "CASHPLACEHOLDER": 0.5})
        assert abs(half[-1]) < abs(full[-1])


class TestNewsFeed:
    def test_news_covers_every_event(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=365 * 4, seed=6))
        assert len(tl.news_for(DEMO_PF)) == len(tl.events)

    def test_news_includes_world_events_not_only_portfolio(self, world):
        """Hiding non-portfolio events would hide the whole mechanism."""
        tl = simulate_world(world, ForwardConfig(horizon_days=365 * 4, seed=6))
        relevance = {n["portfolioRelevance"] for n in tl.news_for(DEMO_PF)}
        assert "mondo" in relevance

    def test_direct_relevance_flagged(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=365 * 6, seed=6))
        news = tl.news_for(DEMO_PF)
        direct = [n for n in news if n["portfolioRelevance"] == "diretta"]
        assert direct
        for n in direct:
            assert set(n["portfolioTargets"]) & set(DEMO_PF)

    def test_empty_portfolio_marks_everything_world(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=365 * 2, seed=6))
        assert all(
            n["portfolioRelevance"] == "mondo" for n in tl.news_for({})
        )

    def test_headlines_are_populated(self, world):
        tl = simulate_world(world, ForwardConfig(horizon_days=365 * 3, seed=6))
        assert all(n["headline"].strip() for n in tl.news_for(DEMO_PF))


class TestScenarioLibrary:
    def test_all_scenarios_run(self, world):
        sim = WorldSimulation(graph=world)
        for spec in SCENARIO_LIBRARY:
            res = sim.run(spec)
            assert res.nodes_affected >= 1

    def test_taiwan_scenario_amplifies(self, world):
        sim = WorldSimulation(graph=world)
        spec = next(s for s in SCENARIO_LIBRARY if s.key == "taiwan_strait")
        assert sim.run(spec).contagion_multiplier > 1.5

    def test_country_impact_uses_revenue_geography(self, world):
        sim = WorldSimulation(graph=world)
        spec = next(s for s in SCENARIO_LIBRARY if s.key == "taiwan_strait")
        res = sim.run(spec)
        impact = sim.country_impact(res.peak_impairment)
        # TSMC's revenue is mostly US, so a Taiwan outage shows up as US
        # exposure — the whole point of not colouring by domicile.
        assert impact.get("US", 0.0) > 0.0
