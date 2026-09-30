"""Tests for the strategy lab: screening rules, weighting, generated code."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from algohns.modules.strategy_lab import (
    WEIGHTINGS,
    FilterExpressionError,
    ScreenCriteria,
    build_weights,
    demo_universe,
    evaluate_filter_expression,
    generate_strategy_code,
    screen_universe,
)


def _universe() -> pd.DataFrame:
    return pd.DataFrame([
        {"ticker": "AAA", "sector": "Tech", "country": "US", "beta": 1.60,
         "market_cap": 3.0e12, "volatility": 0.34, "dividend_yield": 0.000},
        {"ticker": "BBB", "sector": "Staples", "country": "US", "beta": 0.55,
         "market_cap": 3.0e11, "volatility": 0.12, "dividend_yield": 0.031},
        {"ticker": "CCC", "sector": "Energy", "country": "GB", "beta": 0.95,
         "market_cap": 2.0e11, "volatility": 0.26, "dividend_yield": 0.045},
        {"ticker": "DDD", "sector": "Tech", "country": "DE", "beta": 1.05,
         "market_cap": 5.0e10, "volatility": 0.22, "dividend_yield": 0.012},
    ])


class TestCriteriaValidation:
    def test_rejects_inverted_beta_band(self):
        with pytest.raises(ValueError, match="beta_min"):
            ScreenCriteria(beta_min=1.5, beta_max=0.5)

    def test_rejects_inverted_cap_band(self):
        with pytest.raises(ValueError, match="market_cap_min"):
            ScreenCriteria(market_cap_min=1e12, market_cap_max=1e9)

    def test_rejects_zero_positions(self):
        with pytest.raises(ValueError, match="max_positions"):
            ScreenCriteria(max_positions=0)

    def test_rejects_sector_both_included_and_excluded(self):
        with pytest.raises(ValueError, match="both included and excluded"):
            ScreenCriteria(sectors=("Tech",), exclude_sectors=("Tech",))

    def test_describe_lists_every_active_rule(self):
        c = ScreenCriteria(beta_max=1.1, market_cap_min=1e11,
                           exclude_sectors=("Energy",), max_positions=5)
        text = " | ".join(c.describe())
        assert "beta <= 1.1" in text and "Energy" in text and "at most 5" in text


class TestScreening:
    def test_beta_ceiling_filters(self):
        got = screen_universe(_universe(), ScreenCriteria(beta_max=1.0))
        assert set(got["ticker"]) == {"BBB", "CCC"}

    def test_market_cap_floor_filters(self):
        got = screen_universe(_universe(), ScreenCriteria(market_cap_min=2.5e11))
        assert set(got["ticker"]) == {"AAA", "BBB"}

    def test_sector_include_and_exclude(self):
        inc = screen_universe(_universe(), ScreenCriteria(sectors=("Tech",)))
        assert set(inc["ticker"]) == {"AAA", "DDD"}
        exc = screen_universe(_universe(), ScreenCriteria(exclude_sectors=("Tech", "Energy")))
        assert set(exc["ticker"]) == {"BBB"}

    def test_volatility_ceiling_and_dividend_floor(self):
        got = screen_universe(_universe(), ScreenCriteria(max_volatility=0.25,
                                                          min_dividend_yield=0.01))
        assert set(got["ticker"]) == {"BBB", "DDD"}

    def test_country_filter(self):
        got = screen_universe(_universe(), ScreenCriteria(countries=("DE", "GB")))
        assert set(got["ticker"]) == {"CCC", "DDD"}

    def test_position_cap_keeps_the_largest(self):
        got = screen_universe(_universe(), ScreenCriteria(max_positions=2))
        assert set(got["ticker"]) == {"AAA", "BBB"}      # by market cap

    def test_missing_column_skips_its_rule_instead_of_emptying(self):
        thin = _universe()[["ticker", "sector"]]
        got = screen_universe(thin, ScreenCriteria(beta_max=0.1, market_cap_min=1e15))
        assert len(got) == len(thin), "absent columns must not filter everything out"

    def test_empty_universe_returns_empty(self):
        assert screen_universe(pd.DataFrame(), ScreenCriteria()).empty


class TestExpressionSafety:
    def test_valid_expression_filters(self):
        mask = evaluate_filter_expression(_universe(), "beta < 1.0 & volatility < 0.2")
        assert mask.tolist() == [False, True, False, False]

    def test_blank_expression_keeps_everything(self):
        assert evaluate_filter_expression(_universe(), "").all()

    @pytest.mark.parametrize("bad", [
        "__import__('os').system('ls')",
        "import os",
        "lambda x: x",
        "eval('1')",
        "open('/etc/passwd')",
        "globals()",
    ])
    def test_dangerous_expressions_are_rejected(self, bad):
        with pytest.raises(FilterExpressionError):
            evaluate_filter_expression(_universe(), bad)

    def test_non_boolean_expression_is_rejected(self):
        with pytest.raises(FilterExpressionError, match="boolean"):
            evaluate_filter_expression(_universe(), "beta + 1")

    def test_unknown_column_is_reported_not_swallowed(self):
        with pytest.raises(FilterExpressionError):
            evaluate_filter_expression(_universe(), "nonexistent > 1")


class TestWeighting:
    @pytest.mark.parametrize("scheme", sorted(WEIGHTINGS))
    def test_every_scheme_sums_to_one(self, scheme):
        w = build_weights(_universe(), scheme)
        assert sum(w.values()) == pytest.approx(1.0)
        assert all(v >= 0 for v in w.values())

    def test_inverse_vol_favours_the_calmest_name(self):
        w = build_weights(_universe(), "inverse_vol")
        assert max(w, key=w.get) == "BBB"      # lowest volatility

    def test_market_cap_favours_the_largest(self):
        w = build_weights(_universe(), "market_cap")
        assert max(w, key=w.get) == "AAA"

    def test_inverse_beta_favours_the_most_defensive(self):
        w = build_weights(_universe(), "inverse_beta")
        assert max(w, key=w.get) == "BBB"

    def test_missing_column_degrades_to_equal_weight(self):
        thin = _universe()[["ticker"]]
        w = build_weights(thin, "inverse_vol")
        assert sum(w.values()) == pytest.approx(1.0)
        assert len(set(round(v, 10) for v in w.values())) == 1

    def test_degenerate_values_degrade_to_equal_weight(self):
        df = _universe().copy()
        df["volatility"] = 0.0          # all zero -> unusable
        w = build_weights(df, "inverse_vol")
        assert sum(w.values()) == pytest.approx(1.0)

    def test_empty_universe_gives_no_weights(self):
        assert build_weights(pd.DataFrame(), "equal") == {}

    def test_unknown_scheme_raises(self):
        with pytest.raises(KeyError, match="unknown weighting"):
            build_weights(_universe(), "kelly")


class TestGeneratedCode:
    def test_generated_strategy_is_valid_python(self):
        c = ScreenCriteria(beta_max=1.2, market_cap_min=1e11,
                           exclude_sectors=("Energy",), max_positions=8)
        src = generate_strategy_code(c, "inverse_vol", "M", profile="Balanced")
        compile(src, "<generated>", "exec")      # must be runnable Python

    def test_generated_code_embeds_the_actual_rules(self):
        c = ScreenCriteria(beta_max=0.93, max_positions=7,
                           expression="dividend_yield > 0.02")
        src = generate_strategy_code(c, "equal", "Q")
        assert "beta_max=0.93" in src
        assert "max_positions=7" in src
        assert "dividend_yield > 0.02" in src
        assert "'equal'" in src and "'Q'" in src

    def test_generated_code_documents_the_rules_in_the_docstring(self):
        c = ScreenCriteria(beta_max=1.1, exclude_sectors=("Energy",))
        src = generate_strategy_code(c, "equal", "Q", profile="Cautious")
        assert "profile: Cautious" in src
        assert "beta <= 1.1" in src

    def test_unknown_weighting_is_rejected(self):
        with pytest.raises(KeyError):
            generate_strategy_code(ScreenCriteria(), "not_a_scheme")


class TestDemoUniverse:
    def test_demo_universe_carries_real_attributes(self):
        u = demo_universe()
        assert not u.empty
        for col in ("ticker", "sector", "beta", "market_cap", "volatility"):
            assert col in u.columns
        assert pd.to_numeric(u["market_cap"], errors="coerce").gt(0).any()
        assert pd.to_numeric(u["beta"], errors="coerce").gt(0).any()

    def test_screening_the_real_universe_returns_names(self):
        u = demo_universe()
        got = screen_universe(u, ScreenCriteria(beta_max=1.1, market_cap_min=1e11,
                                                max_positions=10))
        assert 0 < len(got) <= 10
        w = build_weights(got, "inverse_vol")
        assert sum(w.values()) == pytest.approx(1.0)
