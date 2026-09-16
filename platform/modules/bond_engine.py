"""MODULE 1 — European Bond Yield & Multi-Tax Engine.

Computes gross and **net** yield-to-maturity for BTP / Bund / OAT / Bonos /
Eurobonds, with the Italian tax treatment modelled properly: coupons and
issue discount as `redditi di capitale` (no loss offset), redemption gain as
`reddito diverso` (offsettable against a `zainetto fiscale`).

Net YTM is solved as the IRR of the *after-tax* cash-flow stream rather than
by discounting the gross yield, because taxing coupons and the redemption
gain at different effective points in time is not expressible as a simple
haircut on gross yield.

Math is pure NumPy/SciPy. `quantlib_bridge()` cross-checks against QuantLib
when it is installed, but nothing here requires it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Literal

from scipy.optimize import brentq

from core.daycount import DayCount, day_count_factor, year_fraction
from core.taxation import (
    FiscalResidence,
    IncomeKind,
    InstrumentClass,
    TaxEngine,
    classify,
)

__all__ = ["Bond", "CashFlow", "BondAnalytics", "analyse", "coupon_schedule"]

# Yield search bracket. -99% to +200% covers deeply distressed and
# hyperinflationary quotes without letting brentq wander into nonsense.
_Y_LOW, _Y_HIGH = -0.99, 2.0


@dataclass(frozen=True)
class Bond:
    """A fixed-coupon bullet bond.

    `clean_price` is quoted per 100 of face, matching market convention.
    Zero-coupon bonds (BOT, CTZ) are expressed with `coupon_rate=0.0` and
    `frequency=1`.
    """

    isin: str
    name: str
    maturity: date
    coupon_rate: float                    # annual, decimal (0.0375 = 3.75%)
    clean_price: float                    # per 100 face
    frequency: int = 2                    # coupons per year
    face: float = 100.0
    issuer_country: str = "IT"
    is_government: bool = True
    is_supranational: bool = False
    issue_date: date | None = None
    issue_price: float | None = None      # for scarto di emissione
    day_count: DayCount = DayCount.ACT_ACT_ICMA

    def __post_init__(self) -> None:
        if self.frequency not in (1, 2, 4, 12):
            raise ValueError(f"unsupported coupon frequency {self.frequency}")
        if self.clean_price <= 0:
            raise ValueError(f"clean_price must be positive, got {self.clean_price}")
        if self.face <= 0:
            raise ValueError(f"face must be positive, got {self.face}")

    @property
    def tax_class(self) -> InstrumentClass:
        return classify(self.issuer_country, self.is_government, self.is_supranational)

    @property
    def coupon_amount(self) -> float:
        """Cash per coupon payment, per `face`."""
        return self.face * self.coupon_rate / self.frequency

    @property
    def is_zero_coupon(self) -> bool:
        return self.coupon_rate == 0.0


@dataclass(frozen=True)
class CashFlow:
    pay_date: date
    gross: float
    net: float
    kind: Literal["coupon", "redemption"]
    years: float           # time from settlement, for discounting
    tax: float = 0.0


@dataclass
class BondAnalytics:
    """Full analytics bundle for one bond at one settlement date."""

    bond: Bond
    settlement: date
    accrued_gross: float
    accrued_net: float
    dirty_price: float
    ytm_gross: float
    ytm_net: float
    macaulay_duration: float
    modified_duration: float
    convexity: float
    total_tax: float
    tax_class: InstrumentClass
    residence: FiscalResidence
    cash_flows: list[CashFlow] = field(default_factory=list)
    loss_carryforward_used: float = 0.0
    stamp_duty_total: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def years_to_maturity(self) -> float:
        return year_fraction(self.settlement, self.bond.maturity)

    @property
    def ytm_net_effective(self) -> float:
        """Net YTM restated as an effective annual rate.

        Nominal yields are only comparable at equal coupon frequency; this
        is the figure to use when ranking an annual-pay Bund against a
        semiannual BTP.
        """
        f = self.bond.frequency
        if math.isnan(self.ytm_net):
            return float("nan")
        return (1.0 + self.ytm_net / f) ** f - 1.0

    @property
    def tax_drag_bps(self) -> float:
        """Gross-to-net yield give-up, in basis points."""
        return (self.ytm_gross - self.ytm_net) * 10_000

    def price_change(self, dy: float) -> float:
        """Second-order price response to a yield shift `dy` (decimal).

        Uses the standard duration+convexity expansion, expressed as a
        fraction of dirty price.
        """
        return -self.modified_duration * dy + 0.5 * self.convexity * dy**2

    def summary(self) -> dict[str, float | str]:
        return {
            "isin": self.bond.isin,
            "name": self.bond.name,
            "maturity": self.bond.maturity.isoformat(),
            "clean_price": round(self.bond.clean_price, 4),
            "accrued": round(self.accrued_gross, 4),
            "dirty_price": round(self.dirty_price, 4),
            "ytm_gross_pct": round(self.ytm_gross * 100, 4),
            "ytm_net_pct": round(self.ytm_net * 100, 4),
            "ytm_net_eff_pct": round(self.ytm_net_effective * 100, 4),
            "tax_drag_bps": round(self.tax_drag_bps, 1),
            "mod_duration": round(self.modified_duration, 4),
            "convexity": round(self.convexity, 4),
            "tax_class": self.tax_class.value,
        }


def coupon_schedule(bond: Bond, settlement: date) -> list[date]:
    """Remaining coupon dates strictly after `settlement`, ascending.

    Generated *backwards* from maturity so the final period always lands
    exactly on the redemption date, which is how these bonds are actually
    structured.
    """
    if settlement >= bond.maturity:
        return []
    step_months = 12 // bond.frequency
    dates: list[date] = []
    cursor = bond.maturity
    # Walk back until we pass settlement, with a generous iteration cap.
    for _ in range(int(bond.frequency * 120) + 4):
        if cursor <= settlement:
            break
        dates.append(cursor)
        cursor = _shift_months(cursor, -step_months)
    return sorted(dates)


def _shift_months(d: date, months: int) -> date:
    """Month arithmetic clamped to end-of-month."""
    total = (d.year * 12 + (d.month - 1)) + months
    year, month = divmod(total, 12)
    month += 1
    # Clamp day to the target month's length.
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)
    last_day = (next_month_start - timedelta(days=1)).day
    return date(year, month, min(d.day, last_day))


def _previous_coupon_date(bond: Bond, settlement: date, schedule: list[date]) -> date:
    """Start of the coupon period containing `settlement`."""
    step_months = 12 // bond.frequency
    if schedule:
        return _shift_months(schedule[0], -step_months)
    return _shift_months(bond.maturity, -step_months)


def _time_axis(bond: Bond, settlement: date, schedule: list[date]) -> list[float]:
    """Discounting times in **coupon periods**, the market YTM convention.

    Street/ICMA yield counts time as `(w + k) / f`, where `w` is the
    unexpired fraction of the current coupon period. Calendar time
    (ACT/365) is *not* equivalent: because actual semiannual periods run
    181-184 days, a calendar axis prices a par bond a few bp away from its
    coupon rate. Period counting reproduces QuantLib's
    ActualActual(ISMA) / Bloomberg convention exactly.
    """
    if not schedule:
        return []
    step_months = 12 // bond.frequency
    next_cpn = schedule[0]
    prev_cpn = _shift_months(next_cpn, -step_months)
    period_days = (next_cpn - prev_cpn).days
    if period_days <= 0:
        raise ValueError(f"degenerate coupon period {prev_cpn} -> {next_cpn}")
    # Unexpired fraction of the period containing settlement.
    w = (next_cpn - settlement).days / period_days
    return [(w + k) / bond.frequency for k in range(len(schedule))]


def accrued_interest(bond: Bond, settlement: date) -> tuple[float, date, date]:
    """Accrued coupon at settlement, plus the enclosing coupon period."""
    schedule = coupon_schedule(bond, settlement)
    next_cpn = schedule[0] if schedule else bond.maturity
    prev_cpn = _previous_coupon_date(bond, settlement, schedule)
    if bond.is_zero_coupon:
        return 0.0, prev_cpn, next_cpn
    frac = day_count_factor(
        prev_cpn,
        settlement,
        bond.day_count,
        period_start=prev_cpn,
        period_end=next_cpn,
        frequency=bond.frequency,
    )
    # frac is already per-year under ICMA; scale back to a period fraction.
    accrued = bond.face * bond.coupon_rate * frac
    return accrued, prev_cpn, next_cpn


def _irr(
    flows: Iterable[tuple[float, float]], price: float, frequency: int = 2
) -> float:
    """Solve for the **nominal annual** rate compounded `frequency` times.

    `flows` are (periods-expressed-in-years, amount) pairs. Discounting uses
    `(1 + y/f)^(t*f)`, which is the street convention: a par bond returns
    exactly its coupon rate. Discounting `(1+y)^t` instead would return the
    *effective* annual yield (4.04% for a 4% semiannual par bond) — a
    correct number, but not the one bond markets quote.

    Returns NaN when the cash-flow signs admit no root in the bracket.
    """
    flows = list(flows)
    if not flows or price <= 0:
        return float("nan")

    def pv(y: float) -> float:
        yp = y / frequency
        return sum(cf / (1.0 + yp) ** (t * frequency) for t, cf in flows) - price

    lo, hi = pv(_Y_LOW), pv(_Y_HIGH)
    if lo * hi > 0:
        return float("nan")
    return brentq(pv, _Y_LOW, _Y_HIGH, xtol=1e-12, rtol=1e-12, maxiter=200)


def analyse(
    bond: Bond,
    settlement: date | None = None,
    *,
    residence: FiscalResidence = FiscalResidence.IT,
    loss_carryforward: float = 0.0,
    include_stamp_duty: bool = False,
) -> BondAnalytics:
    """Full gross/net analytics for `bond` at `settlement`.

    Net cash flows are built as:
      * each coupon  -> coupon - tax(coupon, CAPITAL_INCOME)
      * redemption   -> face + final coupon
                        - tax(face - purchase_clean, OTHER_INCOME)
    The redemption-gain tax is the only component eligible for loss offset,
    matching TUIR treatment.
    """
    settlement = settlement or date.today()
    warnings: list[str] = []
    if settlement >= bond.maturity:
        raise ValueError(
            f"settlement {settlement} is at or past maturity {bond.maturity}"
        )

    schedule = coupon_schedule(bond, settlement)
    accrued, prev_cpn, next_cpn = accrued_interest(bond, settlement)
    dirty = bond.clean_price + accrued

    tax_engine = TaxEngine(residence=residence, loss_carryforward=loss_carryforward)
    cls = bond.tax_class

    gross_flows: list[tuple[float, float]] = []
    net_flows: list[tuple[float, float]] = []
    cash_flows: list[CashFlow] = []
    total_tax = 0.0

    coupon = bond.coupon_amount
    axis = _time_axis(bond, settlement, schedule)
    for i, pay_date in enumerate(schedule):
        t = axis[i]
        is_last = i == len(schedule) - 1
        gross = coupon if not bond.is_zero_coupon else 0.0

        coupon_tax = (
            tax_engine.tax_on(gross, IncomeKind.CAPITAL_INCOME, cls) if gross else 0.0
        )
        net = gross - coupon_tax
        total_tax += coupon_tax

        gain_tax = 0.0
        if is_last:
            # Redemption: principal plus the capital-gain tax, which is the
            # offsettable bucket.
            redemption_gain = bond.face - bond.clean_price
            gain_tax = tax_engine.tax_on(
                redemption_gain, IncomeKind.OTHER_INCOME, cls
            )
            total_tax += gain_tax
            gross += bond.face
            net += bond.face - gain_tax
            kind: Literal["coupon", "redemption"] = "redemption"
            if redemption_gain < 0:
                warnings.append(
                    "Prezzo sopra la pari: alla scadenza si genera una "
                    "minusvalenza, accreditata al zainetto fiscale."
                )
        else:
            kind = "coupon"

        gross_flows.append((t, gross))
        net_flows.append((t, net))
        cash_flows.append(
            CashFlow(
                pay_date=pay_date,
                gross=gross,
                net=net,
                kind=kind,
                years=t,
                tax=coupon_tax + (gain_tax if is_last else 0.0),
            )
        )

    if not schedule:
        raise ValueError("no remaining cash flows before maturity")

    # Scarto di emissione: taxed as capital income, not offsettable.
    if bond.issue_price is not None and bond.issue_price < bond.face:
        discount = bond.face - bond.issue_price
        warnings.append(
            f"Scarto di emissione {discount:.2f} per 100 trattato come "
            "reddito di capitale (non compensabile)."
        )

    # Stamp duty is a real annual levy, so it stays on the calendar axis.
    years_total = year_fraction(settlement, bond.maturity, bond.day_count)
    stamp_total = (
        tax_engine.stamp_duty(dirty, years_total) if include_stamp_duty else 0.0
    )

    ytm_gross = _irr(gross_flows, dirty, bond.frequency)
    ytm_net = _irr(net_flows, dirty + stamp_total, bond.frequency)
    if math.isnan(ytm_gross):
        warnings.append("YTM lordo non risolvibile nel bracket [-99%, +200%].")
    if math.isnan(ytm_net):
        warnings.append("YTM netto non risolvibile nel bracket [-99%, +200%].")

    mac, mod, cvx = _risk_measures(bond, gross_flows, dirty, ytm_gross)

    return BondAnalytics(
        bond=bond,
        settlement=settlement,
        accrued_gross=accrued,
        accrued_net=accrued
        * (1 - tax_engine.profile.rate_for(IncomeKind.CAPITAL_INCOME, cls)),
        dirty_price=dirty,
        ytm_gross=ytm_gross,
        ytm_net=ytm_net,
        macaulay_duration=mac,
        modified_duration=mod,
        convexity=cvx,
        total_tax=total_tax,
        tax_class=cls,
        residence=residence,
        cash_flows=cash_flows,
        loss_carryforward_used=tax_engine.carryforward_used,
        stamp_duty_total=stamp_total,
        warnings=warnings,
    )


def _risk_measures(
    bond: Bond,
    flows: list[tuple[float, float]],
    dirty: float,
    ytm: float,
) -> tuple[float, float, float]:
    """Macaulay duration, modified duration and convexity.

    Worked in coupon periods with per-period yield y_p = y/f, then converted
    to annual units:
        ModDur    = [Sum n_i CF_i /(1+y_p)^(n_i+1)] / (P * f)
        Convexity = [Sum n_i(n_i+1) CF_i /(1+y_p)^(n_i+2)] / (P * f^2)
    """
    if math.isnan(ytm) or dirty <= 0:
        return float("nan"), float("nan"), float("nan")

    f = bond.frequency
    y_p = ytm / f
    if y_p <= -1.0:
        return float("nan"), float("nan"), float("nan")

    pv_sum = 0.0
    d1 = 0.0
    d2 = 0.0
    for t, cf in flows:
        n = t * f
        disc = (1.0 + y_p) ** n
        pv = cf / disc
        pv_sum += pv
        d1 += n * cf / (1.0 + y_p) ** (n + 1)
        d2 += n * (n + 1) * cf / (1.0 + y_p) ** (n + 2)

    if pv_sum <= 0:
        return float("nan"), float("nan"), float("nan")

    macaulay = sum(t * (cf / (1.0 + y_p) ** (t * f)) for t, cf in flows) / pv_sum
    modified = d1 / (pv_sum * f)
    convexity = d2 / (pv_sum * f * f)
    return macaulay, modified, convexity


def quantlib_bridge(bond: Bond, settlement: date) -> dict[str, float] | None:
    """Cross-check YTM/duration against QuantLib when available.

    Returns None if QuantLib is not installed, so callers can treat it as an
    optional validation step rather than a dependency.
    """
    try:
        import QuantLib as ql  # type: ignore
    except ImportError:
        return None

    ql_settle = ql.Date(settlement.day, settlement.month, settlement.year)
    ql.Settings.instance().evaluationDate = ql_settle
    ql_maturity = ql.Date(
        bond.maturity.day, bond.maturity.month, bond.maturity.year
    )
    tenor = {1: ql.Period(ql.Annual), 2: ql.Period(ql.Semiannual),
             4: ql.Period(ql.Quarterly), 12: ql.Period(ql.Monthly)}[bond.frequency]
    schedule = ql.Schedule(
        ql_settle, ql_maturity, tenor, ql.TARGET(),
        ql.Following, ql.Following, ql.DateGeneration.Backward, False,
    )
    ql_bond = ql.FixedRateBond(
        0, bond.face, schedule, [bond.coupon_rate], ql.ActualActual(ql.ActualActual.ISMA)
    )
    dc = ql.ActualActual(ql.ActualActual.ISMA)
    ytm = ql_bond.bondYield(bond.clean_price, dc, ql.Compounded, bond.frequency)
    rate = ql.InterestRate(ytm, dc, ql.Compounded, bond.frequency)
    return {
        "ytm": ytm,
        "modified_duration": ql.BondFunctions.duration(
            ql_bond, rate, ql.Duration.Modified
        ),
        "convexity": ql.BondFunctions.convexity(ql_bond, rate),
    }
