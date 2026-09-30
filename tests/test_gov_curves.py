"""Tests for the global government bond curves.

No network: the FRED path is exercised through a monkeypatched fetch, and the
instrument-level path reads the committed LSEG snapshot. That keeps the suite
runnable in CI exactly as it runs locally.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from algohns.modules import gov_curves as gc


# --------------------------------------------------------------- registry
class TestRegistry:
    def test_every_market_names_its_instrument_and_series(self):
        for code, m in gc.MARKETS.items():
            assert m.code == code and m.name and m.instrument and m.currency
            assert m.fred_10y and m.fred_10y.startswith("IRLTLT01")
            assert m.lseg_file and m.lseg_file.endswith(".csv")

    def test_core_markets_are_covered(self):
        for code in ("IT", "DE", "FR", "ES", "GB", "US", "JP"):
            assert code in gc.MARKETS

    def test_us_curve_series_span_bills_to_thirty_years(self):
        tenors = sorted(gc.FRED_US_CURVE)
        assert tenors[0] < 0.1 and tenors[-1] == 30.0
        assert len(tenors) >= 10


# ------------------------------------------------------- Nelson-Siegel
class TestNelsonSiegel:
    def test_recovers_a_curve_it_generated(self):
        """Fit a known Nelson-Siegel curve back to its own parameters."""
        truth = gc.NelsonSiegelFit(4.0, -2.0, 1.5, 2.0, 0.0, 0)
        years = np.array([0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30])
        fit = gc.fit_nelson_siegel(years, truth.evaluate(years))
        assert fit.rmse < 1e-6
        for t in (1.0, 5.0, 10.0, 30.0):
            assert fit.evaluate(t)[0] == pytest.approx(truth.evaluate(t)[0], abs=1e-6)

    def test_factors_have_their_documented_meaning(self):
        fit = gc.NelsonSiegelFit(4.0, -2.0, 0.0, 2.0, 0.0, 0)
        assert fit.long_run_level == pytest.approx(4.0)
        assert fit.short_rate == pytest.approx(2.0)          # b0 + b1
        # The curve tends to b0 at the long end.
        assert fit.evaluate(200.0)[0] == pytest.approx(4.0, abs=0.05)
        # ... and to b0 + b1 at the short end.
        assert fit.evaluate(1e-4)[0] == pytest.approx(2.0, abs=0.01)

    def test_mad_rejection_removes_a_planted_outlier_block(self):
        """RMSE thresholds miss a cluster of outliers; MAD must not."""
        truth = gc.NelsonSiegelFit(4.5, -2.0, 0.5, 2.5, 0.0, 0)
        years = np.linspace(0.5, 30, 60)
        yields = truth.evaluate(years).copy()
        # Plant real-yield-style outliers (what a linker looks like).
        yields[:12] -= 3.0
        fit = gc.fit_nelson_siegel(years, yields)
        assert fit.n_dropped >= 10, "MAD rejection should discard the block"
        assert fit.rmse < 0.05
        assert fit.evaluate(10.0)[0] == pytest.approx(truth.evaluate(10.0)[0], abs=0.10)

    def test_robust_can_be_disabled(self):
        years = np.linspace(0.5, 30, 40)
        yields = gc.NelsonSiegelFit(4.0, -1.5, 0.5, 2.0, 0.0, 0).evaluate(years)
        yields[0] += 5.0
        assert gc.fit_nelson_siegel(years, yields, robust=False).n_dropped == 0

    def test_too_few_points_is_rejected(self):
        with pytest.raises(ValueError, match="at least 4"):
            gc.fit_nelson_siegel([1.0, 2.0], [3.0, 3.1])

    def test_non_finite_input_is_filtered(self):
        years = np.array([1, 2, 5, 10, 30, np.nan])
        yields = np.array([2.0, 2.5, 3.0, 3.4, 3.8, 9.9])
        assert gc.fit_nelson_siegel(years, yields, robust=False).n_points == 5


# ------------------------------------------- real committed LSEG snapshot
class TestLsegInstrumentCurve:
    def test_italy_snapshot_is_available(self):
        assert "IT" in gc.available_lseg_markets()

    def test_italy_curve_fits_a_plausible_sovereign_shape(self):
        curve = gc.lseg_term_structure("IT", settlement=date(2026, 9, 30))
        assert curve.source == "lseg"
        assert curve.n_instruments > 50
        assert curve.fit is not None
        # A robust fit on real quotes must be tight.
        assert curve.fit.rmse < 0.25, f"loose fit: {curve.fit.rmse}"
        y2, y10, y30 = (curve.yield_at(t) for t in (2, 10, 30))
        # Upward-sloping nominal curve, all yields in a sane band.
        assert 0.0 < y2 < y10 < y30 < 10.0

    def test_italy_ten_year_matches_the_independent_auction_print(self):
        """Cross-source validation: LSEG instrument quotes vs auction results.

        The Italian 10y auction on 2026-09-29 cleared at 4.58% and the 5y at
        4.08% (FXStreet via the Bigdata connector). The curve is fitted from a
        completely separate dataset - LSEG bid quotes - so agreement is real
        evidence the fit is right, not a tautology.
        """
        curve = gc.lseg_term_structure("IT", settlement=date(2026, 9, 30))
        assert curve.yield_at(10) == pytest.approx(4.58, abs=0.35)
        assert curve.yield_at(5) == pytest.approx(4.08, abs=0.35)

    def test_sub_sovereign_issuers_are_excluded(self):
        """Municipal paper trades at a credit spread; it is not a govt curve."""
        curve = gc.lseg_term_structure("IT", settlement=date(2026, 9, 30))
        assert curve.n_instruments < 246, "should not use the whole export"

    def test_unknown_market_raises(self):
        with pytest.raises(KeyError, match="no LSEG export"):
            gc.lseg_term_structure("ZZ")

    def test_fitted_curve_is_smooth_and_monotone_in_the_belly(self):
        curve = gc.lseg_term_structure("IT", settlement=date(2026, 9, 30))
        fitted = curve.fitted_curve()
        assert len(fitted) > 50
        belly = fitted[(fitted["years"] >= 2) & (fitted["years"] <= 20)]
        assert belly["yield"].is_monotonic_increasing


# ---------------------------------------------------------- FRED (mocked)
class TestFredPath:
    @staticmethod
    def _fake_series(sid: str) -> pd.Series:
        idx = pd.date_range("2020-01-01", periods=60, freq="MS")
        base = {"IRLTLT01ITM156N": 3.9, "IRLTLT01DEM156N": 2.3,
                "IRLTLT01USM156N": 4.2}.get(sid, 3.0)
        return pd.Series(base + np.linspace(0, 0.5, 60), index=idx, name=sid)

    def test_benchmark_history_builds_a_country_frame(self, monkeypatch):
        monkeypatch.setattr(gc, "_fetch_fred", self._fake_series)
        frame = gc.fred_benchmark_history(["IT", "DE", "US"])
        assert list(frame.columns) == ["Italy", "Germany", "United States"]
        assert len(frame) == 60

    def test_one_dead_series_does_not_kill_the_rest(self, monkeypatch):
        def flaky(sid: str):
            if sid == "IRLTLT01DEM156N":
                raise RuntimeError("503")
            return self._fake_series(sid)
        monkeypatch.setattr(gc, "_fetch_fred", flaky)
        frame = gc.fred_benchmark_history(["IT", "DE", "US"])
        assert "Germany" not in frame.columns
        assert "Italy" in frame.columns and "United States" in frame.columns

    def test_concurrent_fetch_preserves_requested_order(self, monkeypatch):
        """Fetches run in a thread pool, so column order must not follow completion."""
        import time as _time

        def slow_for_italy(sid: str) -> pd.Series:
            # Make Italy finish last; it must still be the first column.
            if sid == "IRLTLT01ITM156N":
                _time.sleep(0.05)
            return TestFredPath._fake_series(sid)

        monkeypatch.setattr(gc, "_fetch_fred", slow_for_italy)
        frame = gc.fred_benchmark_history(["IT", "DE", "US"])
        assert list(frame.columns) == ["Italy", "Germany", "United States"]

    def test_total_failure_raises_so_the_caller_can_fall_back(self, monkeypatch):
        monkeypatch.setattr(gc, "_fetch_fred",
                            lambda sid: (_ for _ in ()).throw(RuntimeError("down")))
        with pytest.raises(RuntimeError, match="no FRED benchmark"):
            gc.fred_benchmark_history(["IT"])

    def test_us_term_structure_is_fitted(self, monkeypatch):
        def curve_point(sid: str) -> pd.Series:
            level = {"DGS1MO": 4.3, "DGS3MO": 4.3, "DGS6MO": 4.2, "DGS1": 4.1,
                     "DGS2": 4.0, "DGS3": 4.0, "DGS5": 4.1, "DGS7": 4.2,
                     "DGS10": 4.3, "DGS20": 4.6, "DGS30": 4.7}[sid]
            return pd.Series([level], index=pd.to_datetime(["2026-09-29"]), name=sid)
        monkeypatch.setattr(gc, "_fetch_fred", curve_point)
        curve = gc.fred_us_term_structure()
        assert curve.source == "fred" and curve.code == "US"
        assert curve.n_instruments == len(gc.FRED_US_CURVE)
        assert curve.as_of == "2026-09-29"
        assert curve.yield_at(10) == pytest.approx(4.3, abs=0.2)

    def test_history_falls_back_to_bundled_without_network(self, monkeypatch):
        monkeypatch.setattr(gc, "_fetch_fred",
                            lambda sid: (_ for _ in ()).throw(RuntimeError("blocked")))
        frame, status = gc.load_benchmark_history(["IT", "US"])
        assert status == "bundled"
        assert "United States" in frame.columns and not frame.empty


# ------------------------------------------------------------ assembly
class TestAssembly:
    def test_term_structures_report_missing_markets_instead_of_hiding_them(self):
        curves, notes = gc.load_term_structures(allow_live=False)
        assert any(c.code == "IT" for c in curves)
        # Every market without an export must be explained, not silently absent.
        assert len(notes) >= 5
        assert any("lseg_curves" in n for n in notes)

    def test_spread_table_quotes_against_the_benchmark(self):
        curves, _ = gc.load_term_structures(["IT"], allow_live=False)
        table = gc.spread_table(curves, benchmark="IT")
        assert list(table["Market"]) == ["Italy"]
        assert table["Spread vs IT (bps)"].iloc[0] == pytest.approx(0.0)
        assert table["Source"].iloc[0] == "lseg"

    def test_spread_is_none_when_the_benchmark_is_absent(self):
        curves, _ = gc.load_term_structures(["IT"], allow_live=False)
        table = gc.spread_table(curves, benchmark="DE")
        assert table["Spread vs DE (bps)"].isna().all()

    def test_curve_metrics_report_slope_and_inversion(self):
        curves, _ = gc.load_term_structures(["IT"], allow_live=False)
        m = gc.curve_metrics(curves[0])
        assert m["10y"] is not None and m["2s10s"] is not None
        assert m["inverted"] in (0.0, 1.0)

    def test_inverted_curve_is_flagged(self):
        inverted = gc.CountryCurve(
            "XX", "Test", "Bond", "lseg",
            pd.DataFrame({"years": [2, 5, 10, 30], "yield": [5.0, 4.5, 4.0, 3.8]}),
            fit=gc.NelsonSiegelFit(3.8, 1.4, 0.0, 2.0, 0.0, 4),
        )
        assert gc.curve_metrics(inverted)["inverted"] == 1.0

    def test_curve_without_fit_refuses_to_extrapolate(self):
        sparse = gc.CountryCurve("XX", "Test", "Bond", "bundled",
                                 pd.DataFrame({"years": [5.0], "yield": [3.0]}))
        assert sparse.yield_at(30.0) is None
        assert sparse.fitted_curve().empty
