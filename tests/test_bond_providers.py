"""Tests for the bond data sources.

Section 1 adds rendimentibtp.it as a source alongside Borsa Italiana. Neither
live site is reachable from CI, so the scrapers are tested through their pure
parser (`parse_bond_table`) against synthetic pages, and a CSV import covers
the manual fallback. The provider selection and graceful fallback are tested
directly.
"""
from __future__ import annotations

from datetime import date

import pytest

from algohns.modules.bond_providers import (
    SOURCE_LABELS,
    CsvImportProvider,
    LSEGBundledProvider,
    RendimentiBtpProvider,
    SampleProvider,
    get_provider,
    load_universe,
    parse_bond_csv,
    parse_bond_table,
)
from algohns.modules.bond_data import BondScreener, load_lseg_bundled


def _page(rows_html: str, *, nav=True) -> str:
    nav_tbl = "<table><tr><td>Home</td><td>Mercati</td></tr></table>" if nav else ""
    return f"<html><body>{nav_tbl}<table>{rows_html}</table></body></html>"


RENDIMENTIBTP_ROWS = (
    "<tr><th>BTP</th><th>Scadenza</th><th>Cedola</th><th>Prezzo</th><th>Rend.netto</th></tr>"
    "<tr><td>Btp 1 Ago 2034 3,85%</td><td>01/08/2034</td><td>3,85%</td><td>101,25</td><td>3,10%</td></tr>"
    "<tr><td>Btp 1 Mar 2037 0,95%</td><td>01/03/2037</td><td>0,95%</td><td>72,48</td><td>3,45%</td></tr>"
    "<tr><td>Btp 1 Dic 2030 1,65%</td><td>01/12/2030</td><td>1,65%</td><td>94,10</td><td>2,90%</td></tr>"
)


class TestRendimentibtpParsing:
    def test_all_rows_parsed(self):
        bonds = parse_bond_table(_page(RENDIMENTIBTP_ROWS), market="rendimentibtp")
        assert len(bonds) == 3

    def test_name_only_rows_get_a_synthetic_id(self):
        """rendimentibtp lists by description, not ISIN."""
        bonds = parse_bond_table(_page(RENDIMENTIBTP_ROWS), market="rendimentibtp")
        assert all(b.isin for b in bonds)
        assert all(b.isin.startswith("IT~") for b in bonds)

    def test_prices_use_italian_decimal_comma(self):
        bonds = parse_bond_table(_page(RENDIMENTIBTP_ROWS), market="rendimentibtp")
        assert bonds[0].price == pytest.approx(101.25)
        assert bonds[2].price == pytest.approx(94.10)

    def test_maturities_and_coupons(self):
        bonds = parse_bond_table(_page(RENDIMENTIBTP_ROWS), market="rendimentibtp")
        by_mat = {b.maturity: b for b in bonds}
        assert date(2034, 8, 1) in by_mat
        assert by_mat[date(2034, 8, 1)].coupon == pytest.approx(3.85)

    def test_navigation_table_is_ignored(self):
        """The instrument table is chosen over layout tables by row/ISIN count."""
        bonds = parse_bond_table(_page(RENDIMENTIBTP_ROWS, nav=True), market="rendimentibtp")
        assert len(bonds) == 3

    def test_coupon_recovered_from_name_when_no_column(self):
        html = _page(
            "<tr><th>BTP</th><th>Scadenza</th><th>Prezzo</th></tr>"
            "<tr><td>Btp 1 Ago 2034 3,85%</td><td>01/08/2034</td><td>101,25</td></tr>"
        )
        bonds = parse_bond_table(html, market="rendimentibtp")
        assert bonds[0].coupon == pytest.approx(3.85)

    def test_empty_page_raises(self):
        with pytest.raises(RuntimeError):
            parse_bond_table("<html><body><p>nulla</p></body></html>", market="x")


class TestCsvImport:
    def test_english_headers_and_locale(self):
        csv = ("ISIN,Security Des,PX_LAST,CPN,MATURITY,CRNCY\n"
               "IT0005611741,BTP 3.85 07/01/34,101.25,3.85,07/01/2034,EUR\n"
               "IT0005560948,BTP 4.00 10/30/31,103.10,4.00,10/30/2031,EUR\n")
        # A Bloomberg-style export: US dates. The column disambiguates itself
        # (10/30 has 30 in the second slot), so both parse as MM/DD.
        bonds = parse_bond_csv(csv, default_date_order="us")
        by_isin = {b.isin: b for b in bonds}
        assert by_isin["IT0005611741"].maturity == date(2034, 7, 1)
        assert by_isin["IT0005560948"].maturity == date(2031, 10, 30)
        assert by_isin["IT0005611741"].price == pytest.approx(101.25)

    def test_italian_headers_semicolon_and_title_rows(self):
        csv = ("Report BTP;;;\n;;;\n"
               "Descrizione;Cedola;Scadenza;Prezzo;ISIN\n"
               "Btp 1 Ago 2034 3,85%;3,85;01/08/2034;101,25;IT0005611741\n")
        bonds = parse_bond_csv(csv)
        assert len(bonds) == 1
        assert bonds[0].isin == "IT0005611741"
        assert bonds[0].maturity == date(2034, 8, 1)
        assert bonds[0].price == pytest.approx(101.25)

    def test_name_only_csv(self):
        # A quoted name field keeps its internal comma; maturity comes from
        # the full month word in the name.
        bonds = parse_bond_csv('Name,Price\n"Btp 1 Ago 2034 3,85%",101.25\n')
        assert len(bonds) == 1
        assert bonds[0].maturity == date(2034, 8, 1)
        assert bonds[0].price == pytest.approx(101.25)
        assert bonds[0].isin.startswith("IT~")

    def test_month_word_maturity_from_name(self):
        """rendimentibtp-style names ("Btp 1 Ago 2034") carry the maturity."""
        bonds = parse_bond_csv("Name,Price\nBtp 1 Dic 2030,94.10\n")
        assert bonds[0].maturity == date(2030, 12, 1)

    def test_tab_delimited(self):
        csv = "ISIN\tName\tPrice\tMaturity\nIT0005611741\tBTP 2034\t101.25\t01/07/2034\n"
        bonds = parse_bond_csv(csv)
        assert bonds[0].price == pytest.approx(101.25)

    def test_date_column_overrides_default_order(self):
        """A column proving MM/DD must win over an EU default, and vice versa."""
        us = parse_bond_csv("Name,Maturity\nX,10/30/2031\n", default_date_order="eu")
        assert us[0].maturity == date(2031, 10, 30)
        eu = parse_bond_csv("Name,Maturity\nX,30/10/2031\n", default_date_order="us")
        assert eu[0].maturity == date(2031, 10, 30)

    def test_unrecognised_csv_raises(self):
        with pytest.raises(ValueError, match="not recognised"):
            parse_bond_csv("foo,bar,baz\n1,2,3\n")

    def test_blank_rows_skipped(self):
        csv = "ISIN,Name,Price\n\nIT0005611741,BTP 2034,101.25\n\n"
        assert len(parse_bond_csv(csv)) == 1


class TestProviderRegistry:
    @pytest.mark.parametrize("key", list(SOURCE_LABELS))
    def test_every_source_resolves(self, key):
        assert get_provider(key).key == key

    def test_unknown_provider_raises(self):
        with pytest.raises(KeyError, match="unknown provider"):
            get_provider("nope")

    def test_sample_always_available(self):
        assert SampleProvider().available()

    def test_csv_provider_needs_payload(self):
        assert not CsvImportProvider().available()
        assert CsvImportProvider(payload="Name,Price\nX,100\n").available()

    def test_rendimentibtp_reports_dependency_state(self):
        # available() reflects whether requests+bs4 are importable.
        assert isinstance(RendimentiBtpProvider().available(), bool)


class TestLoadUniverse:
    def test_falls_back_to_sample_when_source_unavailable(self):
        """A dead source must degrade to the bundled universe, not crash."""
        bonds, status = load_universe("csv", csv_payload=None)
        assert status == "sample"
        assert isinstance(bonds, list)

    def test_csv_source_returns_live_status(self):
        bonds, status = load_universe(
            "csv", csv_payload="ISIN,Name,Price,Maturity\nIT0005611741,BTP,101.25,01/07/2034\n"
        )
        assert status == "live:csv"
        assert len(bonds) == 1

    def test_status_names_the_source(self):
        _, status = load_universe(
            "csv", csv_payload="Name,Price\nBTP 2034,101\n"
        )
        assert status.startswith("live:")


class TestLSEGBundled:
    """The committed LSEG snapshot loads offline and carries cross-check data.

    No network: the provider reads the CSV shipped in the repo, so these run
    in CI exactly as they do locally.
    """

    def test_source_is_registered(self):
        assert "lseg" in SOURCE_LABELS
        assert get_provider("lseg").key == "lseg"

    def test_snapshot_loads_with_real_btps(self):
        bonds = load_lseg_bundled()
        assert bonds, "the LSEG snapshot CSV should be bundled in the repo"
        # It is a real Italian sovereign universe, so BTPs must be present.
        assert any("Italy" in b.name for b in bonds)
        assert all(b.market == "lseg" for b in bonds)

    def test_carries_lseg_crosscheck_fields(self):
        bonds = load_lseg_bundled()
        priced = [b for b in bonds if b.price and b.lseg_yield is not None]
        assert priced, "at least some rows carry a price and an LSEG yield"
        assert any(b.lseg_mod_duration is not None for b in priced)
        assert any(b.gspread is not None for b in priced)

    def test_provider_reports_available_and_fetches(self):
        prov = LSEGBundledProvider()
        assert prov.available() is True
        assert len(prov.fetch()) == len(load_lseg_bundled())

    def test_load_universe_status_is_live_lseg(self):
        bonds, status = load_universe("lseg")
        assert status == "live:lseg"
        assert len(bonds) > 100  # the export is a broad comparables set

    def test_build_table_emits_crosscheck_columns(self):
        view = BondScreener().build_table(load_lseg_bundled(), tax_key="IT_GOV_WHITELIST")
        for col in ("LSEG Yld%", "LSEG ModDur", "G-Spread", "YTMΔ(bps)"):
            assert col in view.columns
        # For a priced BTP the engine's YTM must land near LSEG's bid yield:
        # the cross-check delta is small for the bulk of the curve.
        delta = view["YTMΔ(bps)"].dropna().abs()
        assert not delta.empty
        assert delta.median() < 25  # bps — ICMA engine agrees with LSEG


# ---------------------------------------------------------------------------
# Italian-sovereign-only screener filter (drops municipals/corporates/foreign)
# ---------------------------------------------------------------------------
class TestSovereignFilter:
    def test_recognises_italian_state_series(self):
        from algohns.modules.bond_data import is_italian_sovereign
        for n in ["BTP Tf 3.85% Lg34", "BOT 2025", "CCT-EU 2030", "CTZ 2026",
                  "BTP Italia Nov28"]:
            assert is_italian_sovereign(n), n

    def test_rejects_non_state_issuers(self):
        from algohns.modules.bond_data import is_italian_sovereign
        for n in ["Comune di Foggia 4% 2025", "Città di Torino 3% 2027",
                  "Bund 2.5 2034", "OAT 3% 2033", "Bonos 2035",
                  "Regione Lombardia 2028", "Enel SpA 2030"]:
            assert not is_italian_sovereign(n), n

    def test_filter_drops_municipals_keeps_btps(self):
        import pandas as pd
        from algohns.modules.bond_data import italian_sovereigns_only
        df = pd.DataFrame({
            "ISIN": ["IT0001", "IT0002", "DE0003"],
            "Name": ["BTP Tf 3.85% Lg34", "Città di Torino 3% 2027", "Bund 2034"],
        })
        out = italian_sovereigns_only(df)
        assert list(out["Name"]) == ["BTP Tf 3.85% Lg34"]
