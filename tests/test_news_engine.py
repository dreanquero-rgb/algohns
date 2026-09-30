"""Tests for the combinatorial news engine.

The point of the engine is scale with structure: thousands of distinct events
whose text and quantitative impact always agree, drawn reproducibly. These
tests pin exactly that.
"""
from __future__ import annotations

from collections import Counter

import pytest

from algohns.modules.news_engine import (
    MAGNITUDES,
    NEWS_FRAMES,
    catalogue_stats,
    expected_market_drag,
    news_kinds,
    sample_news,
)

SUBJECTS = {
    "company": [f"TICK{i}" for i in range(40)],
    "sector": ["Semis", "Software", "Energy", "Financials", "Health Care"],
    "country": ["US", "IT", "DE", "GB", "JP", "CN"],
    "region": ["Europe", "Asia", "Americas", "EMEA"],
    "chokepoint": ["Suez Canal", "Strait of Hormuz", "Taiwan Strait"],
}


class TestCatalogueScale:
    def test_event_space_is_far_larger_than_a_hand_written_list(self):
        """The brief was at least ~1000 more events than the original ~50."""
        kinds = news_kinds()
        assert len(kinds) >= 1000, f"only {len(kinds)} news kinds"

    def test_distinct_headline_count_is_reported(self):
        stats = catalogue_stats({k: len(v) for k, v in SUBJECTS.items()})
        assert stats["kinds"] >= 1000
        assert stats["distinct_headlines"] > stats["kinds"]
        assert stats["frames"] == len(NEWS_FRAMES)
        assert stats["categories"] >= 6

    def test_keys_are_unique(self):
        keys = [k.key for k in news_kinds()]
        assert len(keys) == len(set(keys))

    def test_catalogue_is_cached_and_stable(self):
        assert news_kinds() is news_kinds()


class TestBalance:
    def test_a_meaningful_share_is_favourable(self):
        """A generator of pure disaster produces an unwinnable, useless test."""
        stats = catalogue_stats()
        assert 0.20 <= stats["favourable_share"] <= 0.50

    def test_every_category_has_both_signs(self):
        """Otherwise a category is a one-way bet the model can be gamed against."""
        by_cat: dict[str, set[bool]] = {}
        for k in news_kinds():
            by_cat.setdefault(k.category, set()).add(k.favourable)
        multi = [c for c, signs in by_cat.items() if len(signs) == 2]
        assert len(multi) >= 4, f"only {multi} carry both favourable and adverse news"

    def test_market_drag_is_a_plausible_magnitude(self):
        """Compensated by the simulator, so it must not be wild."""
        drag = expected_market_drag()
        assert -0.40 < drag < 0.0, f"implausible market drag {drag}"

    def test_rate_decomposition_preserves_frame_intensity(self):
        """Splitting a frame into bands must redistribute, not multiply, its rate."""
        total_frames = sum(f.annual_rate for f in NEWS_FRAMES)
        total_kinds = sum(k.annual_rate for k in news_kinds())
        assert total_kinds == pytest.approx(total_frames, rel=1e-9)


class TestMagnitudeSemantics:
    def test_severity_scales_the_shock(self):
        """"severe" must actually hit harder than "marginal", not just read so."""
        by = {}
        for k in news_kinds():
            if k.frame_key == "recession":
                by[k.magnitude] = k
        assert set(by) == set(MAGNITUDES)
        assert abs(by["severe"].market[0]) > abs(by["major"].market[0])
        assert abs(by["major"].market[0]) > abs(by["moderate"].market[0])
        assert abs(by["moderate"].market[0]) > abs(by["marginal"].market[0])

    def test_severe_events_are_rarer_than_marginal_ones(self):
        rates: dict[str, float] = Counter()
        for k in news_kinds():
            rates[k.magnitude] += k.annual_rate
        assert rates["severe"] < rates["major"] < rates["moderate"] < rates["marginal"]

    def test_vol_multiplier_rises_with_severity(self):
        assert (MAGNITUDES["marginal"]["vol"] < MAGNITUDES["moderate"]["vol"]
                < MAGNITUDES["major"]["vol"] < MAGNITUDES["severe"]["vol"])

    def test_impairment_never_exceeds_total_loss(self):
        for k in news_kinds():
            assert 0.0 <= k.impairment[0] <= k.impairment[1] <= 1.0


class TestSampling:
    def test_same_seed_reproduces_the_timeline(self):
        a = sample_news(365 * 2, SUBJECTS, seed=11)
        b = sample_news(365 * 2, SUBJECTS, seed=11)
        assert [(n.day, n.kind.key, n.headline) for n in a] == \
               [(n.day, n.kind.key, n.headline) for n in b]

    def test_different_seeds_give_different_worlds(self):
        a = sample_news(365 * 3, SUBJECTS, seed=1)
        b = sample_news(365 * 3, SUBJECTS, seed=2)
        assert [n.headline for n in a] != [n.headline for n in b]

    def test_events_are_dated_in_range_and_sorted(self):
        horizon = 500
        evs = sample_news(horizon, SUBJECTS, seed=5)
        assert evs, "a multi-year horizon should draw some news"
        assert all(0 <= n.day < horizon for n in evs)
        assert [n.day for n in evs] == sorted(n.day for n in evs)

    def test_headline_names_its_subject(self):
        for n in sample_news(365, SUBJECTS, seed=9):
            if n.kind.scope != "global":
                assert n.subject in n.headline

    def test_intensity_scales_the_flow(self):
        calm = sample_news(365 * 3, SUBJECTS, seed=13, intensity=0.4)
        wild = sample_news(365 * 3, SUBJECTS, seed=13, intensity=3.0)
        assert len(wild) > len(calm) * 2

    def test_shocks_lie_inside_their_declared_bands(self):
        for n in sample_news(365 * 3, SUBJECTS, seed=17):
            assert n.kind.equity[0] - 1e-9 <= n.equity_shock <= n.kind.equity[1] + 1e-9
            assert n.kind.market[0] - 1e-9 <= n.market_shock <= n.kind.market[1] + 1e-9
            assert 0.0 <= n.impairment <= 1.0
            assert n.duration_days >= n.kind.duration_days[0]

    def test_active_window_is_consistent(self):
        n = sample_news(400, SUBJECTS, seed=19)[0]
        assert n.active_on(n.day)
        assert not n.active_on(n.end_day)
        assert n.end_day == n.day + n.duration_days

    def test_variety_is_high_across_a_long_horizon(self):
        """Few repeats is the whole reason for the combinatorial space."""
        evs = sample_news(365 * 5, SUBJECTS, seed=23)
        unique = len({n.headline for n in evs})
        assert unique / len(evs) > 0.85, "headlines repeat too often"

    def test_rejects_bad_arguments(self):
        with pytest.raises(ValueError, match="horizon_days"):
            sample_news(0, SUBJECTS)
        with pytest.raises(ValueError, match="intensity"):
            sample_news(365, SUBJECTS, intensity=0.0)

    def test_missing_subject_pool_is_skipped_not_crashed(self):
        evs = sample_news(365 * 2, {"company": ["ONLY"]}, seed=3)
        assert all(n.kind.scope in ("company", "global") for n in evs)
