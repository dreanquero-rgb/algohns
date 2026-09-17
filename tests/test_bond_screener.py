"""Regression tests for the bond screener's parsing and filtering.

The bug these pin: the screener reported "Live data from Borsa Italiana — 45
instruments" and then showed an empty table. Three defects stacked up.

1. `float(df["Years"].dropna().max() or 30)` looks like it defaults a missing
   column to 30 years, but `max()` on an all-NaN column returns NaN, NaN is
   truthy, so `or 30` never fires. The slider bound became NaN and
   `between(0, NaN)` is all-False.
2. Maturity parsing accepted only four slash-separated numeric formats, so a
   dash- or month-name date left `Years` entirely NaN — which triggered (1).
3. `_num` stripped commas unconditionally, reading the Italian price
   "101,25" as 10125. That one is worse than an empty table: it yields a
   plausible-looking number and a silently wrong yield.

All three lived in code with no test coverage, so the filtering logic is now
pure functions rather than Streamlit glue.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from algohns.modules.bond_data import (
    DEFAULT_MAX_YEARS,
    _num,
    _parse_date,
    filter_screener,
    screener_year_bounds,
)


def _frame(years, **overrides) -> pd.DataFrame:
    n = len(years)
    data = {
        "ISIN": [f"IT000000000{i}" for i in range(n)],
        "Country": ["IT"] * n,
        "Type": ["govt"] * n,
        "Price": [101.2] * n,
        "Years": years,
        "NetYTM%": [3.1] * n,
    }
    data.update(overrides)
    return pd.DataFrame(data)


class TestNumberParsing:
    @pytest.mark.parametrize("raw,expected", [
        ("101.25", 101.25),      # English decimal point
        ("1,234.56", 1234.56),   # English thousands + decimal
        ("101,25", 101.25),      # Italian decimal comma
        ("1.234,56", 1234.56),   # Italian thousands + decimal
        ("99,9", 99.9),
        ("3,85%", 3.85),
        ("  102,34  ", 102.34),
        ("1,234", 1234.0),       # three trailing digits reads as thousands
    ])
    def test_both_locales(self, raw, expected):
        assert _num(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", ["-", "--", "n.a.", "n.d.", "", "   ", "abc"])
    def test_missing_values(self, raw):
        assert _num(raw) is None

    def test_italian_price_is_not_inflated_hundredfold(self):
        """The defect that mattered most: 101,25 must not become 10125."""
        assert _num("101,25") == pytest.approx(101.25)
        assert _num("101,25") != pytest.approx(10125.0)

    def test_domain_bound_resolves_the_ambiguous_case(self):
        """"102,340" is 102340 in English and 102.34 in Italian.

        Context-free the thousands reading wins, which is right in general and
        absurd for a bond price. The price call site passes a bound.
        """
        assert _num("102,340") == pytest.approx(102340.0)
        assert _num("102,340", max_plausible=10_000) == pytest.approx(102.34)

    def test_coupon_bound(self):
        assert _num("3,850", max_plausible=100) == pytest.approx(3.85)


class TestDateParsing:
    @pytest.mark.parametrize("raw", [
        "01/07/2034", "2034-07-01", "01-07-2034", "01.07.2034",
        "01/07/34", "01 lug 2034", "1-Jul-2034",
    ])
    def test_formats_accepted(self, raw):
        assert _parse_date(raw) == date(2034, 7, 1)

    @pytest.mark.parametrize("raw", ["-", "", "n.d.", "rubbish", "32/13/2034"])
    def test_unparseable(self, raw):
        assert _parse_date(raw) is None

    def test_italian_month_names(self):
        assert _parse_date("15 dic 2030") == date(2030, 12, 15)
        assert _parse_date("01 gen 2028") == date(2028, 1, 1)


class TestYearBounds:
    def test_all_nan_column_falls_back(self):
        """The NaN-truthiness trap: this returned NaN and broke the filter."""
        bound = screener_year_bounds(_frame([np.nan, np.nan, np.nan]))
        assert bound == DEFAULT_MAX_YEARS
        assert np.isfinite(bound)

    def test_missing_column_falls_back(self):
        assert screener_year_bounds(pd.DataFrame({"ISIN": ["X"]})) == DEFAULT_MAX_YEARS

    def test_empty_frame_falls_back(self):
        assert screener_year_bounds(pd.DataFrame({"Years": []})) == DEFAULT_MAX_YEARS

    def test_uses_the_real_maximum(self):
        assert screener_year_bounds(_frame([1.5, 9.25, 4.0])) == pytest.approx(9.3)

    def test_bound_always_covers_the_longest_bond(self):
        """Rounding to nearest would drop the longest-dated instrument.

        round(9.25, 1) is 9.2, so a bound computed that way sits below the
        bond that produced it and `between(0, 9.2)` filters it out.
        """
        for years in ([9.25], [1.0, 12.349], [0.04], [29.999]):
            bound = screener_year_bounds(_frame(years))
            assert bound >= max(years), f"{bound} < {max(years)}"
            kept = filter_screener(_frame(years), ["IT"], ["govt"], (0.0, bound), 0.0)
            assert len(kept) == len(years)

    def test_partial_nan_uses_known_values(self):
        assert screener_year_bounds(_frame([np.nan, 7.4, np.nan])) == pytest.approx(7.4)

    def test_nonpositive_maximum_falls_back(self):
        assert screener_year_bounds(_frame([0.0, -1.0])) == DEFAULT_MAX_YEARS


class TestScreenerFilter:
    def test_unparsed_maturities_are_kept_not_dropped(self):
        """The reported symptom: 45 loaded, 0 shown."""
        df = _frame([np.nan] * 3)
        out = filter_screener(df, ["IT"], ["govt"],
                              (0.0, screener_year_bounds(df)), 0.0)
        assert len(out) == 3

    def test_mixed_frame_keeps_everything_at_full_range(self):
        df = _frame([2.0, np.nan, 12.5])
        out = filter_screener(df, ["IT"], ["govt"], (0.0, 30.0), 0.0)
        assert len(out) == 3

    def test_raising_the_lower_bound_excludes_unknown_maturities(self):
        """Once the user asks for a minimum, a blank maturity cannot qualify."""
        df = _frame([2.0, np.nan, 12.5])
        out = filter_screener(df, ["IT"], ["govt"], (5.0, 30.0), 0.0)
        assert set(out["Years"].dropna()) == {12.5}
        assert out["Years"].isna().sum() == 0

    def test_range_filters_on_known_values(self):
        df = _frame([1.0, 5.0, 9.0])
        out = filter_screener(df, ["IT"], ["govt"], (2.0, 6.0), 0.0)
        assert list(out["Years"]) == [5.0]

    def test_country_and_type_filters(self):
        df = _frame([1.0, 2.0], Country=["IT", "DE"], Type=["govt", "eurobond"])
        assert len(filter_screener(df, ["IT"], ["govt"], (0.0, 30.0), 0.0)) == 1
        assert len(filter_screener(df, ["IT", "DE"], ["govt", "eurobond"],
                                   (0.0, 30.0), 0.0)) == 2

    def test_min_net_ytm(self):
        df = _frame([1.0, 2.0], **{"NetYTM%": [1.0, 4.0]})
        out = filter_screener(df, ["IT"], ["govt"], (0.0, 30.0), 3.0)
        assert list(out["NetYTM%"]) == [4.0]

    def test_min_net_ytm_zero_keeps_unpriced_rows(self):
        df = _frame([1.0, 2.0], **{"NetYTM%": [np.nan, 4.0]})
        assert len(filter_screener(df, ["IT"], ["govt"], (0.0, 30.0), 0.0)) == 2

    def test_nan_range_is_ignored_rather_than_excluding_everything(self):
        """Defensive: a NaN bound must never empty the table again."""
        df = _frame([1.0, 2.0, 3.0])
        out = filter_screener(df, ["IT"], ["govt"], (0.0, float("nan")), 0.0)
        assert len(out) == 3

    def test_empty_frame_returns_empty(self):
        assert filter_screener(pd.DataFrame(), ["IT"], ["govt"], (0.0, 30.0), 0.0).empty

    def test_no_filters_returns_everything(self):
        df = _frame([1.0, 2.0, 3.0])
        assert len(filter_screener(df)) == 3
