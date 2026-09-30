"""Global government bond curves: BTP vs Bund vs OAT vs Bonos vs Gilt vs UST.

**Where the data comes from, and how it stays current.** Three sources sit
behind one interface, tried in order, so the page always renders something real
and always says which source it used:

1. ``lseg`` — **instrument-level curves** built from LSEG "Comparable Bonds"
   exports committed under ``algohns/data/lseg_curves/``. This is the highest
   quality input: real ISINs, real bid prices, real bid yields, so the curve is
   bootstrapped from actual tradable instruments rather than a single benchmark
   point. Drop one export per country (``scripts/build_lseg_dataset.py``) and
   that country appears. Refreshed by re-running the export.

2. ``fred`` — **live, keyless, multi-country**. FRED publishes the OECD
   10-year benchmark series for every market we care about
   (``IRLTLT01{CC}M156N``) and the full US Treasury curve (``DGS*``), with no
   API key and no rate cap that a dashboard would hit. This is what keeps the
   cross-country comparison current on the deployed app.

3. ``bundled`` — the committed ``us10y_monthly.csv`` and the Italian LSEG
   snapshot, so the page still works with no network at all.

**Why not the MCP connectors.** They were the first thing checked. Alpha
Vantage covers the US Treasury curve well but its free tier is 25 requests/day,
which cannot serve a public dashboard; Bigdata.com's country tearsheets state
plainly that treasury yields are US-only. Neither can produce a euro-area or
gilt curve. So the connectors are used to *pull snapshots during development*,
and FRED is the live path in production.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from ..config import get_settings

log = logging.getLogger(__name__)

__all__ = [
    "MARKETS",
    "FRED_BENCHMARK_10Y",
    "FRED_US_CURVE",
    "CountryCurve",
    "load_benchmark_history",
    "load_term_structures",
    "spread_table",
    "curve_metrics",
    "available_lseg_markets",
    "lseg_term_structure",
    "NelsonSiegelFit",
    "fit_nelson_siegel",
]

_DATA = get_settings().data_dir
_LSEG_DIR = _DATA / "lseg_curves"


# ---------------------------------------------------------------------------
# Market registry.  `lseg_file` is the export filename this market expects, so
# adding a country is a data drop rather than a code change.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Market:
    code: str
    name: str
    instrument: str            # what the market's benchmark bond is called
    currency: str
    fred_10y: str | None = None
    lseg_file: str | None = None


MARKETS: dict[str, Market] = {
    "IT": Market("IT", "Italy", "BTP", "EUR", "IRLTLT01ITM156N", "IT.csv"),
    "DE": Market("DE", "Germany", "Bund", "EUR", "IRLTLT01DEM156N", "DE.csv"),
    "FR": Market("FR", "France", "OAT", "EUR", "IRLTLT01FRM156N", "FR.csv"),
    "ES": Market("ES", "Spain", "Bonos", "EUR", "IRLTLT01ESM156N", "ES.csv"),
    "GB": Market("GB", "United Kingdom", "Gilt", "GBP", "IRLTLT01GBM156N", "GB.csv"),
    "US": Market("US", "United States", "Treasury", "USD", "IRLTLT01USM156N", "US.csv"),
    "JP": Market("JP", "Japan", "JGB", "JPY", "IRLTLT01JPM156N", "JP.csv"),
    "CA": Market("CA", "Canada", "GoC", "CAD", "IRLTLT01CAM156N", "CA.csv"),
    "AU": Market("AU", "Australia", "ACGB", "AUD", "IRLTLT01AUM156N", "AU.csv"),
    "CH": Market("CH", "Switzerland", "Confederation", "CHF",
                 "IRLTLT01CHM156N", "CH.csv"),
}

FRED_BENCHMARK_10Y: dict[str, str] = {
    c: m.fred_10y for c, m in MARKETS.items() if m.fred_10y
}

# The full US Treasury curve, daily. Maturity in years -> FRED series.
FRED_US_CURVE: dict[float, str] = {
    1 / 12: "DGS1MO", 0.25: "DGS3MO", 0.5: "DGS6MO", 1.0: "DGS1",
    2.0: "DGS2", 3.0: "DGS3", 5.0: "DGS5", 7.0: "DGS7",
    10.0: "DGS10", 20.0: "DGS20", 30.0: "DGS30",
}

_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
_TIMEOUT = 20


# ---------------------------------------------------------------------------
# Nelson-Siegel curve fitting
#
# A government curve must be *fitted*, not interpolated. Raw instrument yields
# are a noisy scatter: off-the-run bonds, linkers, stale quotes and liquidity
# premia all sit a few basis points off the true curve, so reading a 30-year
# yield straight off the two nearest bonds can produce a shape that does not
# exist. Nelson-Siegel (1987) is the standard parsimonious fit, with factors
# that mean something:
#
#     y(tau) = b0 + b1 * (1 - e^-t)/t + b2 * ((1 - e^-t)/t - e^-t),  t = tau/lam
#
#   b0        the long-run level the curve flattens to
#   b0 + b1   the instantaneous short rate
#   b2        the curvature (the hump), positive for a mid-curve bulge
#   lam       where the hump sits, in years
#
# lam enters non-linearly, so it is grid-searched while the three betas are
# solved by OLS at each candidate - the Diebold-Li approach, which is fast and
# has no convergence failures.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NelsonSiegelFit:
    """A fitted Nelson-Siegel curve with its diagnostics."""

    beta0: float
    beta1: float
    beta2: float
    lam: float
    rmse: float
    n_points: int
    n_dropped: int = 0

    @property
    def long_run_level(self) -> float:
        return self.beta0

    @property
    def short_rate(self) -> float:
        return self.beta0 + self.beta1

    def evaluate(self, years) -> np.ndarray:
        """Fitted yield at one or many maturities, in percent."""
        arr = np.atleast_1d(np.asarray(years, dtype=float))
        basis = _ns_basis(arr, self.lam)
        return basis @ np.array([self.beta0, self.beta1, self.beta2])


def _ns_basis(years: np.ndarray, lam: float) -> np.ndarray:
    t = np.maximum(np.asarray(years, dtype=float), 1e-6) / max(lam, 1e-6)
    loading_slope = (1.0 - np.exp(-t)) / t
    loading_curve = loading_slope - np.exp(-t)
    return np.column_stack([np.ones_like(t), loading_slope, loading_curve])


def fit_nelson_siegel(
    years, yields, *, robust: bool = True, max_resid_sigma: float = 3.0,
    max_iterations: int = 4,
) -> NelsonSiegelFit:
    """Fit Nelson-Siegel by grid-searching lambda and OLS on the betas.

    With `robust`, outliers are rejected iteratively using a **MAD-based** scale
    rather than the RMSE. That distinction matters: RMSE is itself inflated by
    the outliers it is supposed to detect, so an RMSE threshold rejects almost
    nothing. The median absolute deviation is unaffected by a minority of bad
    points, so ``1.4826 * MAD`` (the consistent estimator of sigma under
    normality) gives a threshold that actually bites.

    What this removes, without needing to hardcode instrument taxonomy:

    * inflation-linked bonds, whose quoted yield is a *real* yield and sits far
      below the nominal curve;
    * stale quotes carried over from an earlier rate regime;
    * illiquid off-the-run issues trading at a wide concession.

    All three are the same statistical problem - a point that does not belong to
    the nominal government curve - so one robust estimator handles them all.
    """
    x = np.asarray(years, dtype=float)
    y = np.asarray(yields, dtype=float)
    keep = np.isfinite(x) & np.isfinite(y) & (x > 0)
    x, y = x[keep], y[keep]
    if x.size < 4:
        raise ValueError("Nelson-Siegel needs at least 4 usable points")

    def _fit(xx: np.ndarray, yy: np.ndarray) -> tuple[np.ndarray, float, float]:
        best: tuple[np.ndarray, float, float] | None = None
        for lam in np.concatenate([np.linspace(0.3, 6.0, 58),
                                   np.linspace(6.5, 20.0, 28)]):
            basis = _ns_basis(xx, lam)
            beta, *_ = np.linalg.lstsq(basis, yy, rcond=None)
            rmse = float(np.sqrt(np.mean((yy - basis @ beta) ** 2)))
            if best is None or rmse < best[1]:
                best = (beta, rmse, float(lam))
        assert best is not None
        return best

    beta, rmse, lam = _fit(x, y)
    n_original = x.size
    if robust:
        for _ in range(max_iterations):
            resid = y - _ns_basis(x, lam) @ beta
            mad = float(np.median(np.abs(resid - np.median(resid))))
            scale = 1.4826 * mad
            if scale <= 1e-9:
                break
            mask = np.abs(resid) <= max_resid_sigma * scale
            if mask.all() or mask.sum() < 4:
                break
            x, y = x[mask], y[mask]
            beta, rmse, lam = _fit(x, y)
    return NelsonSiegelFit(float(beta[0]), float(beta[1]), float(beta[2]),
                           lam, rmse, int(x.size), int(n_original - x.size))


# ---------------------------------------------------------------------------
# Term structure container
# ---------------------------------------------------------------------------
@dataclass
class CountryCurve:
    """A term structure for one market at one moment."""

    code: str
    name: str
    instrument: str
    source: str                       # "lseg" | "fred" | "bundled"
    points: pd.DataFrame = field(default_factory=pd.DataFrame)  # years, yield
    as_of: str | None = None
    n_instruments: int = 0
    fit: "NelsonSiegelFit | None" = None

    def yield_at(self, years: float) -> float | None:
        """Fitted yield at `years`, from Nelson-Siegel when a fit exists.

        Falls back to linear interpolation (a single benchmark point, or too few
        instruments to fit) and refuses to extrapolate beyond the observed range
        in that case, so a number is never invented.
        """
        if self.fit is not None:
            return float(self.fit.evaluate(years)[0])
        if self.points.empty:
            return None
        pts = self.points.dropna().sort_values("years")
        if pts.empty or not (pts["years"].min() <= years <= pts["years"].max()):
            return None
        return float(np.interp(years, pts["years"], pts["yield"]))

    def fitted_curve(self, max_years: float | None = None) -> pd.DataFrame:
        """A smooth fitted curve for plotting, over the observed maturity range."""
        if self.fit is None or self.points.empty:
            return pd.DataFrame()
        hi = max_years or float(self.points["years"].max())
        grid = np.linspace(max(float(self.points["years"].min()), 0.05), hi, 120)
        return pd.DataFrame({"years": grid, "yield": self.fit.evaluate(grid)})


# ---------------------------------------------------------------------------
# FRED (live, keyless)
# ---------------------------------------------------------------------------
def _fetch_fred(series_id: str, timeout: int = _TIMEOUT) -> pd.Series:
    """Download one FRED series as a dated float Series.

    Keyless CSV endpoint. Raises on any failure so the caller can fall back —
    the page must never present a silent empty curve as real data.
    """
    import requests

    resp = requests.get(_FRED_CSV.format(sid=series_id), timeout=timeout,
                        headers={"User-Agent": "Algohns/1.0 (research)"})
    resp.raise_for_status()
    frame = pd.read_csv(io.StringIO(resp.text))
    if frame.shape[1] < 2:
        raise ValueError(f"unexpected FRED payload for {series_id}")
    date_col, value_col = frame.columns[0], frame.columns[1]
    out = pd.Series(
        pd.to_numeric(frame[value_col], errors="coerce").values,
        index=pd.to_datetime(frame[date_col], errors="coerce"),
        name=series_id,
    ).dropna()
    out = out[~out.index.isna()].sort_index()
    if out.empty:
        raise ValueError(f"FRED returned no observations for {series_id}")
    return out


def fred_benchmark_history(codes: list[str] | None = None) -> pd.DataFrame:
    """10-year benchmark yields per country, monthly, as far back as published."""
    wanted = [c for c in (codes or list(FRED_BENCHMARK_10Y)) if c in FRED_BENCHMARK_10Y]
    series: dict[str, pd.Series] = {}
    for code in wanted:
        try:
            series[MARKETS[code].name] = _fetch_fred(FRED_BENCHMARK_10Y[code])
        except Exception as exc:  # noqa: BLE001 - one dead series must not kill the rest
            log.info("FRED %s (%s) unavailable: %s", code, FRED_BENCHMARK_10Y[code], exc)
    if not series:
        raise RuntimeError("no FRED benchmark series could be fetched")
    return pd.DataFrame(series).sort_index()


def fred_us_term_structure() -> CountryCurve:
    """The full US Treasury curve from its latest published observation."""
    rows: list[dict] = []
    as_of: str | None = None
    for years, sid in FRED_US_CURVE.items():
        try:
            s = _fetch_fred(sid)
            rows.append({"years": years, "yield": float(s.iloc[-1]),
                         "label": sid})
            as_of = max(as_of or "", s.index[-1].date().isoformat())
        except Exception as exc:  # noqa: BLE001
            log.info("FRED %s unavailable: %s", sid, exc)
    if not rows:
        raise RuntimeError("no FRED US curve points could be fetched")
    m = MARKETS["US"]
    pts = pd.DataFrame(rows).sort_values("years").reset_index(drop=True)
    try:
        ns_fit = fit_nelson_siegel(pts["years"], pts["yield"])
    except Exception:  # noqa: BLE001
        ns_fit = None
    return CountryCurve(m.code, m.name, m.instrument, "fred", pts,
                        as_of, len(pts), ns_fit)


# ---------------------------------------------------------------------------
# LSEG instrument-level curves (committed exports)
# ---------------------------------------------------------------------------
def available_lseg_markets() -> dict[str, str]:
    """Which markets have a committed LSEG export, code -> filename."""
    found: dict[str, str] = {}
    if _LSEG_DIR.exists():
        for code, m in MARKETS.items():
            if m.lseg_file and (_LSEG_DIR / m.lseg_file).exists():
                found[code] = m.lseg_file
    # The original Italian export lives at the top level of the data dir.
    if "IT" not in found and (_DATA / "lseg_btp_comparables.csv").exists():
        found["IT"] = "lseg_btp_comparables.csv"
    return found


def _read_lseg_csv(path) -> pd.DataFrame:
    with open(path, newline="") as fh:
        frame = pd.DataFrame(list(csv.DictReader(fh)))
    for col in ("Price", "Coupon", "LSEGYield", "LSEGModDur", "GSpread"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "Maturity" in frame.columns:
        frame["Maturity"] = pd.to_datetime(frame["Maturity"], errors="coerce")
    return frame


def lseg_term_structure(
    code: str, *, settlement: date | None = None, sovereign_only: bool = True,
) -> CountryCurve:
    """Build a term structure from a market's committed LSEG export.

    Only priced instruments with a future maturity contribute. Sub-sovereign
    issuers (municipalities, regions) are excluded by default: they trade at a
    credit spread to the sovereign, so mixing them in would not be a government
    curve. Instruments inside a month of redemption are dropped because
    annualising a tiny price gap over days produces a meaningless yield.
    """
    found = available_lseg_markets()
    if code not in found:
        raise KeyError(f"no LSEG export committed for {code}")
    m = MARKETS[code]
    path = (_LSEG_DIR / found[code]) if (_LSEG_DIR / found[code]).exists() \
        else (_DATA / found[code])
    frame = _read_lseg_csv(path)
    if frame.empty:
        raise RuntimeError(f"LSEG export for {code} is empty")

    settlement = settlement or date.today()
    frame = frame[frame["Price"].notna() & frame["LSEGYield"].notna()]
    frame = frame[frame["Maturity"].notna()]
    years = (frame["Maturity"] - pd.Timestamp(settlement)).dt.days / 365.25
    frame = frame.assign(years=years)
    frame = frame[frame["years"] > 1 / 12]

    if sovereign_only and "Name" in frame.columns:
        sovereign_token = {"IT": "Italy", "DE": "Germany", "FR": "France",
                           "ES": "Spain", "GB": "United Kingdom",
                           "US": "United States", "JP": "Japan"}.get(code)
        if sovereign_token:
            mask = frame["Name"].str.contains(sovereign_token, case=False, na=False)
            if mask.any():
                frame = frame[mask]

    if frame.empty:
        raise RuntimeError(f"no priced sovereign instruments for {code}")

    as_of = None
    if "QuoteDate" in frame.columns:
        qd = frame["QuoteDate"].dropna()
        as_of = str(qd.max()) if not qd.empty else None

    points = frame[["years", "LSEGYield"]].rename(columns={"LSEGYield": "yield"})
    points = points.sort_values("years").reset_index(drop=True)
    try:
        ns_fit = fit_nelson_siegel(points["years"], points["yield"])
    except Exception as exc:  # noqa: BLE001 - too few points to fit
        log.info("Nelson-Siegel fit failed for %s: %s", code, exc)
        ns_fit = None
    return CountryCurve(m.code, m.name, m.instrument, "lseg", points,
                        as_of, len(points), ns_fit)


# ---------------------------------------------------------------------------
# Bundled fallback
# ---------------------------------------------------------------------------
def bundled_benchmark_history() -> pd.DataFrame:
    """US 10Y from the committed dataset — always available, offline."""
    from .reference_data import us10y

    s = us10y()
    if s.empty:
        return pd.DataFrame()
    return s.rename("United States").to_frame()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def load_benchmark_history(
    codes: list[str] | None = None, *, allow_live: bool = True,
) -> tuple[pd.DataFrame, str]:
    """Cross-country 10-year benchmark history.

    Returns (frame, status) where status is ``live:fred`` or ``bundled``, so the
    page can state its provenance instead of implying freshness it lacks.
    """
    if allow_live:
        try:
            return fred_benchmark_history(codes), "live:fred"
        except Exception as exc:  # noqa: BLE001
            log.info("FRED benchmark history unavailable: %s", exc)
    return bundled_benchmark_history(), "bundled"


def load_term_structures(
    codes: list[str] | None = None, *, allow_live: bool = True,
    settlement: date | None = None,
) -> tuple[list[CountryCurve], list[str]]:
    """Term structures per market, preferring instrument-level LSEG data.

    Returns (curves, notes). `notes` explains, per market, which source was used
    or why a market is missing — the page shows it rather than hiding gaps.
    """
    wanted = codes or list(MARKETS)
    curves: list[CountryCurve] = []
    notes: list[str] = []
    lseg_available = available_lseg_markets()

    for code in wanted:
        if code not in MARKETS:
            continue
        if code in lseg_available:
            try:
                curves.append(lseg_term_structure(code, settlement=settlement))
                continue
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{MARKETS[code].name}: LSEG export unusable ({exc})")
        if code == "US" and allow_live:
            try:
                curves.append(fred_us_term_structure())
                continue
            except Exception as exc:  # noqa: BLE001
                notes.append(f"United States: live curve unavailable ({exc})")
        if code not in lseg_available:
            notes.append(
                f"{MARKETS[code].name}: no instrument-level data — drop an LSEG "
                f"Comparable Bonds export at data/lseg_curves/{MARKETS[code].lseg_file}"
            )
    return curves, notes


def curve_metrics(curve: CountryCurve) -> dict[str, float | None]:
    """Standard shape readings of a term structure.

    The 2s10s slope is the recession signal every rates desk quotes; the 10s30s
    tells you whether the long end is pricing term premium or expectations.
    """
    y2, y5, y10, y30 = (curve.yield_at(t) for t in (2.0, 5.0, 10.0, 30.0))
    out: dict[str, float | None] = {
        "2y": y2, "5y": y5, "10y": y10, "30y": y30,
        "2s10s": (y10 - y2) if (y2 is not None and y10 is not None) else None,
        "10s30s": (y30 - y10) if (y10 is not None and y30 is not None) else None,
        "inverted": None,
    }
    if out["2s10s"] is not None:
        out["inverted"] = float(out["2s10s"] < 0)
    return out


def spread_table(curves: list[CountryCurve], benchmark: str = "DE",
                 tenor: float = 10.0) -> pd.DataFrame:
    """Yield spread to a benchmark market at one tenor, in basis points.

    Spread to the Bund is how euro-area sovereign risk is actually quoted, so
    this is the table a fixed-income desk would read first.
    """
    base = next((c for c in curves if c.code == benchmark), None)
    base_y = base.yield_at(tenor) if base else None
    rows = []
    for c in curves:
        y = c.yield_at(tenor)
        rows.append({
            "Market": c.name,
            "Instrument": c.instrument,
            f"{tenor:g}y yield %": None if y is None else round(y, 3),
            f"Spread vs {benchmark} (bps)": (
                None if (y is None or base_y is None) else round((y - base_y) * 100, 1)
            ),
            "Source": c.source,
            "Instruments": c.n_instruments,
        })
    return pd.DataFrame(rows)
