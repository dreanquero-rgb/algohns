"""Module 1 — European Bond Yield & Multi-Tax Engine.

Precise fixed-income analytics for European government and corporate bonds
(BTP, Bund, OAT, Bonos, Eurobonds) with a *dynamic taxation engine* driven by
the investor's fiscal residence and the instrument type.

What it computes
----------------
* Gross & **net** Yield-to-Maturity (net = after-tax internal rate of return,
  solved as a TIR/XIRR on net cash-flows).
* Accrued interest / rateo cedolare (both gross and net).
* Macaulay duration, Modified duration and Convexity.
* Dynamic taxation: Italian 12.5% (white-list government) vs 26% (corporate),
  handling *disaggio d'emissione* (issue discount) and *minusvalenze*
  (capital-loss tax credits) compensation.

Design
------
The heavy analytics run on pure NumPy/SciPy so the module works everywhere.
If ``QuantLib`` is installed it is used as an independent cross-check of the
pure-python YTM/duration (exposed via :meth:`BondEngine.quantlib_crosscheck`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from ..core.utils import lazy_import, safe_ratio

_scipy_opt = lazy_import("scipy.optimize", pip_name="scipy", reason="solve yields precisely")
_ql = lazy_import("QuantLib", pip_name="QuantLib", reason="cross-check bond math with QuantLib")


# ---------------------------------------------------------------------------
# Tax profiles
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TaxProfile:
    """A taxation regime for one (residence, instrument-type) combination.

    ``coupon_rate``       tax on coupon income (redditi di capitale).
    ``capital_gain_rate`` tax on the redemption / disaggio gain (redditi diversi).
    ``allows_loss_offset``whether capital losses generate a usable credit.
    """

    name: str
    coupon_rate: float
    capital_gain_rate: float
    allows_loss_offset: bool = True
    note: str = ""


# Registry of common European regimes. Rates are indicative and configurable.
TAX_PROFILES: dict[str, TaxProfile] = {
    "IT_GOV_WHITELIST": TaxProfile(
        "Italy — Government / White-list (12.5%)",
        coupon_rate=0.125,
        capital_gain_rate=0.125,
        note="BTP, BOT, CCT and white-list sovereign bonds (e.g. Bund, OAT, Bonos).",
    ),
    "IT_CORPORATE": TaxProfile(
        "Italy — Corporate / Other (26%)",
        coupon_rate=0.26,
        capital_gain_rate=0.26,
        note="Corporate bonds and non white-list issuers.",
    ),
    "IT_SUPRANATIONAL": TaxProfile(
        "Italy — Supranational (12.5%)",
        coupon_rate=0.125,
        capital_gain_rate=0.125,
        note="World Bank, EIB, BEI and similar supranational issuers.",
    ),
    "DE_ABGELTUNGSTEUER": TaxProfile(
        "Germany — Abgeltungsteuer (26.375%)",
        coupon_rate=0.26375,
        capital_gain_rate=0.26375,
        note="Flat rate incl. Solidaritätszuschlag (church tax excluded).",
    ),
    "FR_PFU": TaxProfile(
        "France — PFU / Flat Tax (30%)",
        coupon_rate=0.30,
        capital_gain_rate=0.30,
        note="Prélèvement Forfaitaire Unique.",
    ),
    "GROSS": TaxProfile("Gross — no tax", 0.0, 0.0, note="Pre-tax reference."),
}


# ---------------------------------------------------------------------------
# Bond definition
# ---------------------------------------------------------------------------
@dataclass
class Bond:
    """A plain-vanilla fixed-coupon bond."""

    face_value: float = 100.0
    coupon_rate: float = 0.035          # annual, e.g. 0.035 = 3.5%
    frequency: int = 2                  # coupons per year (1 annual, 2 semi-annual)
    issue_date: date = field(default_factory=lambda: date(2020, 1, 1))
    maturity_date: date = field(default_factory=lambda: date(2030, 1, 1))
    settlement_date: date = field(default_factory=date.today)
    clean_price: float = 100.0          # market clean price (per 100 face)
    issue_price: float = 100.0          # for disaggio d'emissione on gov. bonds
    redemption: float = 100.0           # redemption per 100 face
    isin: str = ""
    name: str = ""

    def coupon_amount(self) -> float:
        """Cash coupon paid at each period (per face_value)."""
        return self.face_value * self.coupon_rate / self.frequency

    def coupon_dates(self) -> list[date]:
        """Future coupon dates strictly after settlement, up to maturity."""
        months = int(round(12 / self.frequency))
        dates: list[date] = []
        d = self.maturity_date
        while d > self.issue_date:
            dates.append(d)
            d = _add_months(d, -months)
        dates = sorted(dates)
        return [d for d in dates if d > self.settlement_date]

    def previous_coupon_date(self) -> date:
        months = int(round(12 / self.frequency))
        future = self.coupon_dates()
        first = future[0] if future else self.maturity_date
        return _add_months(first, -months)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass
class BondAnalytics:
    dirty_price: float
    clean_price: float
    accrued_interest: float
    ytm_gross: float
    ytm_net: float
    macaulay_duration: float
    modified_duration: float
    convexity: float
    tax_profile: str
    total_tax_paid: float
    capital_gain: float
    minusvalenza_credit: float
    cashflow_table: list[dict]

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d.pop("cashflow_table", None)
        return d


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class BondEngine:
    """Analytics engine turning a :class:`Bond` into :class:`BondAnalytics`."""

    def __init__(self, day_count: float = 365.0) -> None:
        self.day_count = day_count

    # --------------------------------------------------------- accrued interest
    def accrued_interest(self, bond: Bond) -> float:
        prev = bond.previous_coupon_date()
        nxt = bond.coupon_dates()
        next_cpn = nxt[0] if nxt else bond.maturity_date
        period_days = max((next_cpn - prev).days, 1)
        elapsed = max((bond.settlement_date - prev).days, 0)
        return bond.coupon_amount() * safe_ratio(elapsed, period_days)

    # ------------------------------------------------------------- cash-flows
    def _cashflows(self, bond: Bond, tax: TaxProfile) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
        """Return (times_in_years, gross_cf, net_cf, meta).

        Net cash-flows apply coupon tax per period and capital-gain tax at
        redemption, including disaggio d'emissione for government profiles.
        """
        dates = bond.coupon_dates()
        coupon = bond.coupon_amount()
        purchase = bond.clean_price + self.accrued_interest(bond)  # dirty price paid

        times, gross, net = [], [], []
        rows = []
        for i, d in enumerate(dates):
            t = (d - bond.settlement_date).days / self.day_count
            g = coupon
            net_coupon = coupon * (1 - tax.coupon_rate)
            is_last = i == len(dates) - 1

            cap_gain = 0.0
            cap_tax = 0.0
            if is_last:
                redemption = bond.redemption * bond.face_value / 100.0
                g += redemption
                # Capital gain taxable base. For government profiles the
                # disaggio d'emissione (redemption vs issue price) is part of
                # the taxable gain; in practice the retail base is
                # (redemption - purchase clean price).
                cap_gain = redemption - bond.clean_price
                cap_tax = max(cap_gain, 0.0) * tax.capital_gain_rate
                net_coupon = coupon * (1 - tax.coupon_rate) + redemption - cap_tax

            times.append(t)
            gross.append(g)
            net.append(net_coupon)
            rows.append(
                {
                    "date": d.isoformat(),
                    "years": round(t, 4),
                    "gross_cf": round(g, 4),
                    "coupon_tax": round(coupon * tax.coupon_rate, 4),
                    "capital_gain_tax": round(cap_tax, 4),
                    "net_cf": round(net_coupon, 4),
                }
            )

        meta = {
            "purchase_dirty": purchase,
            "capital_gain": (bond.redemption * bond.face_value / 100.0) - bond.clean_price,
        }
        return np.array(times), np.array(gross), np.array(net), (meta | {"rows": rows})

    # ------------------------------------------------------------------- YTM
    @staticmethod
    def _pv(rate: float, times: np.ndarray, cfs: np.ndarray) -> float:
        return float(np.sum(cfs / (1.0 + rate) ** times))

    def _solve_yield(self, price: float, times: np.ndarray, cfs: np.ndarray) -> float:
        """Solve annual-compounded IRR such that PV(cfs) == price."""

        def npv(rate: float) -> float:
            return self._pv(rate, times, cfs) - price

        # Try a robust bracketed solver first (SciPy brentq).
        try:
            from scipy.optimize import brentq  # local import; guarded below

            return float(brentq(npv, -0.95, 5.0, maxiter=200, xtol=1e-10))
        except Exception:  # noqa: BLE001 - fall back to Newton
            return self._newton_yield(npv)

    @staticmethod
    def _newton_yield(npv, guess: float = 0.05) -> float:
        rate = guess
        for _ in range(200):
            f = npv(rate)
            # numerical derivative
            h = 1e-6
            fp = (npv(rate + h) - f) / h
            if abs(fp) < 1e-12:
                break
            step = f / fp
            rate -= step
            if abs(step) < 1e-10:
                break
            rate = max(rate, -0.95)
        return float(rate)

    # ----------------------------------------------------- duration/convexity
    def _duration_convexity(self, ytm: float, times: np.ndarray, cfs: np.ndarray, price: float):
        disc = (1.0 + ytm) ** times
        pv = cfs / disc
        weights = pv / price if price else pv * 0
        macaulay = float(np.sum(times * weights))
        modified = macaulay / (1.0 + ytm) if (1.0 + ytm) else 0.0
        convexity = float(
            np.sum(cfs * times * (times + 1) / (1.0 + ytm) ** (times + 2)) / price
        ) if price else 0.0
        return macaulay, modified, convexity

    # -------------------------------------------------------------- analyse
    def analyse(self, bond: Bond, tax_key: str = "IT_GOV_WHITELIST") -> BondAnalytics:
        tax = TAX_PROFILES.get(tax_key, TAX_PROFILES["GROSS"])
        times, gross, net, meta = self._cashflows(bond, tax)
        accrued = self.accrued_interest(bond)
        dirty = bond.clean_price + accrued

        if len(times) == 0:
            raise ValueError("Bond has no remaining cash-flows (check dates).")

        ytm_gross = self._solve_yield(dirty, times, gross)
        ytm_net = self._solve_yield(dirty, times, net)
        macaulay, modified, convexity = self._duration_convexity(ytm_gross, times, gross, dirty)

        total_tax = float(np.sum(gross - net))
        cap_gain = meta["capital_gain"]
        minus_credit = 0.0
        if cap_gain < 0 and tax.allows_loss_offset:
            # Capital loss -> usable tax credit (minusvalenza).
            minus_credit = abs(cap_gain) * tax.capital_gain_rate

        return BondAnalytics(
            dirty_price=round(dirty, 4),
            clean_price=round(bond.clean_price, 4),
            accrued_interest=round(accrued, 4),
            ytm_gross=round(ytm_gross, 6),
            ytm_net=round(ytm_net, 6),
            macaulay_duration=round(macaulay, 4),
            modified_duration=round(modified, 4),
            convexity=round(convexity, 4),
            tax_profile=tax.name,
            total_tax_paid=round(total_tax, 4),
            capital_gain=round(cap_gain, 4),
            minusvalenza_credit=round(minus_credit, 4),
            cashflow_table=meta["rows"],
        )

    # --------------------------------------------------- QuantLib cross-check
    def quantlib_crosscheck(self, bond: Bond) -> dict | None:
        """Independent gross YTM/duration from QuantLib, if installed.

        Returns ``None`` when QuantLib is unavailable so callers can simply
        skip the comparison.
        """
        from ..core.utils import is_available

        if not is_available(_ql):
            return None
        ql = _ql
        try:
            cal = ql.TARGET()
            settle = ql.Date(bond.settlement_date.day, bond.settlement_date.month, bond.settlement_date.year)
            ql.Settings.instance().evaluationDate = settle
            issue = ql.Date(bond.issue_date.day, bond.issue_date.month, bond.issue_date.year)
            maturity = ql.Date(bond.maturity_date.day, bond.maturity_date.month, bond.maturity_date.year)
            tenor = ql.Period(ql.Semiannual if bond.frequency == 2 else ql.Annual)
            schedule = ql.Schedule(
                issue, maturity, tenor, cal,
                ql.Unadjusted, ql.Unadjusted,
                ql.DateGeneration.Backward, False,
            )
            qbond = ql.FixedRateBond(
                2, bond.face_value, schedule, [bond.coupon_rate], ql.ActualActual(ql.ActualActual.ISDA)
            )
            day_counter = ql.ActualActual(ql.ActualActual.ISDA)
            # QuantLib >= 1.43 expects a BondPrice wrapper; older versions take
            # a raw clean price. Try the modern signature first, then fall back.
            try:
                price = ql.BondPrice(bond.clean_price, ql.BondPrice.Clean)
                ytm = qbond.bondYield(price, day_counter, ql.Compounded, ql.Annual)
            except Exception:  # noqa: BLE001
                ytm = qbond.bondYield(bond.clean_price, day_counter, ql.Compounded, ql.Annual)
            rate = ql.InterestRate(ytm, day_counter, ql.Compounded, ql.Annual)
            return {
                "quantlib_ytm": round(ytm, 6),
                "quantlib_accrued": round(qbond.accruedAmount(), 4),
                "quantlib_duration": round(ql.BondFunctions.duration(qbond, rate, ql.Duration.Modified), 4),
                "quantlib_convexity": round(ql.BondFunctions.convexity(qbond, rate), 4),
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"QuantLib cross-check failed: {exc}"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _add_months(d: date, months: int) -> date:
    """Add (possibly negative) months to a date, clamping the day."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    # clamp day to end of month
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


# Convenience façade -----------------------------------------------------------
def analyse_bond(bond: Bond, tax_key: str = "IT_GOV_WHITELIST") -> BondAnalytics:
    """One-shot helper used by the dashboard."""
    return BondEngine().analyse(bond, tax_key=tax_key)
