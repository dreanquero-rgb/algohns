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


class TestNameDerivedFields:
    """Instrument names are a real source, not a fallback of desperation.

    The MOT list page may carry no maturity column at all — that lives on
    each bond's detail page — and Italian government bond names encode both
    the coupon and the maturity.
    """

    @pytest.mark.parametrize("name,expected", [
        # Day-less month code, which is how the listing usually writes it.
        ("BTP TF 3,85% LG34 EUR", date(2034, 7, 1)),
        ("BTP TF 0,95% MZ37 EUR", date(2037, 3, 1)),
        ("BTP€i TF 1,50% MG29", date(2029, 5, 1)),
        # With an explicit day.
        ("BTP-1AG31", date(2031, 8, 1)),
        ("CCT-EU 15OT30", date(2030, 10, 15)),
        ("BOT 14GE27", date(2027, 1, 14)),
        # Plain embedded date.
        ("BTP 3.85% 01/07/2034", date(2034, 7, 1)),
    ])
    def test_maturity_from_name(self, name, expected):
        from algohns.modules.bond_data import maturity_from_name

        assert maturity_from_name(name) == expected

    @pytest.mark.parametrize("name", ["", "nonsense EUR", "BTP", "CCT-EU"])
    def test_no_maturity_in_name(self, name):
        from algohns.modules.bond_data import maturity_from_name

        assert maturity_from_name(name) is None

    @pytest.mark.parametrize("name,expected", [
        ("BTP TF 3,85% LG34 EUR", 3.85),
        ("BTP 4.00% 2031", 4.0),
        ("BTP€i TF 1,50% MG29", 1.5),
    ])
    def test_coupon_from_name(self, name, expected):
        from algohns.modules.bond_data import coupon_from_name

        assert coupon_from_name(name) == pytest.approx(expected)

    @pytest.mark.parametrize("name", ["BOT 14GE27", "CCT-EU 15OT30", ""])
    def test_no_coupon_in_name(self, name):
        from algohns.modules.bond_data import coupon_from_name

        assert coupon_from_name(name) is None


class TestResilientScraping:
    """Extraction must survive the page shapes that broke it.

    The live symptom was 45 ISINs extracted with zero prices and zero
    maturities: ISIN extraction scans the whole row with a regex and never
    touches a column index, while price and maturity depended on a header
    map that came back all-None. These fixtures reproduce each way that map
    can fail.
    """

    ROWS = [
        ("IT0005611741", "BTP TF 3,85% LG34 EUR", "101,25", "01/07/2034"),
        ("IT0005560948", "BTP-1AG31", "103,10", "01/08/2031"),
        ("IT0005425233", "BTP TF 0,95% MZ37 EUR", "72,48", "01/03/2037"),
    ]

    def _page(self, header, *, with_maturity=True, title_row=False, nav_table=False):
        hdr = "".join(f"<th>{h}</th>" for h in header)
        body = ""
        for isin, name, price, mat in self.ROWS:
            cells = [f'<a href="/borsa/scheda/{isin}.html">{name}</a>', price]
            if with_maturity:
                cells.append(mat)
            # Decoys: a small percentage and a clock, which must not be read
            # as the price column.
            cells += ["0,15", "12:30"]
            body += "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        junk = "<tr><td colspan=5>Listino completo</td></tr>" if title_row else ""
        nav = "<table><tr><td>Home</td><td>Mercati</td></tr></table>" if nav_table else ""
        return f"<html><body>{nav}<table>{junk}<tr>{hdr}</tr>{body}</table></body></html>"

    def _fetch(self, html, monkeypatch):
        import types

        from algohns.modules import bond_data as bd

        class _Resp:
            text = html

            def raise_for_status(self):
                pass

        monkeypatch.setattr(bd, "_requests",
                            types.SimpleNamespace(get=lambda *a, **k: _Resp()))
        monkeypatch.setattr(bd, "is_available", lambda m: True)
        return bd.fetch_mot_list("BTP")

    FULL_HEADER = ["Name", "Last price", "Maturity", "Var %", "Time"]

    def test_mappable_header(self, monkeypatch):
        out = self._fetch(self._page(self.FULL_HEADER), monkeypatch)
        assert len(out) == 3
        assert all(b.price is not None for b in out)
        assert all(b.maturity is not None for b in out)
        assert out[0].price == pytest.approx(101.25)

    def test_unmappable_header_falls_back_to_content(self, monkeypatch):
        """Generic labels: columns must be inferred from their values."""
        out = self._fetch(self._page(["A", "B", "C", "D", "E"]), monkeypatch)
        assert len(out) == 3
        assert all(b.price is not None for b in out), "price column not inferred"
        assert all(b.maturity is not None for b in out), "maturity column not inferred"

    def test_no_maturity_column_uses_the_name(self, monkeypatch):
        """The list page may simply not carry a maturity column."""
        out = self._fetch(
            self._page(["Name", "Last price", "Var %", "Time"], with_maturity=False),
            monkeypatch,
        )
        assert len(out) == 3
        assert all(b.maturity is not None for b in out)
        assert out[0].maturity == date(2034, 7, 1)

    def test_header_displaced_by_a_title_row(self, monkeypatch):
        """The header is not always the first <tr>."""
        out = self._fetch(self._page(self.FULL_HEADER, title_row=True), monkeypatch)
        assert len(out) == 3
        assert all(b.price is not None for b in out)

    def test_layout_table_before_the_data_table(self, monkeypatch):
        """`find("table")` picked navigation markup; ISIN count picks the list."""
        out = self._fetch(self._page(self.FULL_HEADER, nav_table=True), monkeypatch)
        assert len(out) == 3
        assert {b.isin for b in out} == {r[0] for r in self.ROWS}

    def test_decoy_columns_are_not_read_as_price(self, monkeypatch):
        out = self._fetch(self._page(["A", "B", "C", "D", "E"]), monkeypatch)
        # 0,15 (a var%) and 12:30 (a clock) sit in the same rows.
        assert all(b.price > 50 for b in out)

    def test_coupon_recovered_from_names(self, monkeypatch):
        out = self._fetch(self._page(self.FULL_HEADER), monkeypatch)
        by_isin = {b.isin: b for b in out}
        assert by_isin["IT0005611741"].coupon == pytest.approx(3.85)
        assert by_isin["IT0005425233"].coupon == pytest.approx(0.95)

    def test_english_formatting(self, monkeypatch):
        html = (
            "<html><body><table>"
            "<tr><th>Name</th><th>Last</th><th>Maturity</th></tr>"
            '<tr><td><a href="/x/IT0005611741.html">BTP 3.85% 2034</a></td>'
            "<td>101.25</td><td>2034-07-01</td></tr>"
            "</table></body></html>"
        )
        out = self._fetch(html, monkeypatch)
        assert out[0].price == pytest.approx(101.25)
        assert out[0].maturity == date(2034, 7, 1)

    def test_rows_without_an_isin_are_skipped(self, monkeypatch):
        html = (
            "<html><body><table>"
            "<tr><th>Name</th><th>Last</th><th>Maturity</th></tr>"
            "<tr><td>Totale</td><td>-</td><td>-</td></tr>"
            '<tr><td><a href="/x/IT0005611741.html">BTP 3.85% LG34</a></td>'
            "<td>101,25</td><td>01/07/2034</td></tr>"
            "</table></body></html>"
        )
        out = self._fetch(html, monkeypatch)
        assert len(out) == 1
