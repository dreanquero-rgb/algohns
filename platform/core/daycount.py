"""Day-count conventions for fixed-income accrual and discounting.

Implemented as pure-Python date arithmetic so the bond engine has no hard
dependency on QuantLib. `quantlib_bridge` cross-checks these against
QuantLib when it is installed.
"""
from __future__ import annotations

from datetime import date
from enum import Enum

__all__ = ["DayCount", "year_fraction", "day_count_factor"]


class DayCount(str, Enum):
    """Supported conventions.

    ACT_ACT_ICMA is the market standard for BTP / Bund / OAT / Bonos and is
    the default throughout the engine. ACT_365F and ACT_360 are provided for
    money-market style instruments and cross-checking.
    """

    ACT_ACT_ICMA = "ACT/ACT-ICMA"
    ACT_365F = "ACT/365F"
    ACT_360 = "ACT/360"
    THIRTY_360 = "30/360"


def _thirty_360_days(start: date, end: date) -> int:
    """US (NASD) 30/360 day difference."""
    d1, d2 = start.day, end.day
    if d1 == 31:
        d1 = 30
    if d2 == 31 and d1 == 30:
        d2 = 30
    return 360 * (end.year - start.year) + 30 * (end.month - start.month) + (d2 - d1)


def day_count_factor(
    start: date,
    end: date,
    convention: DayCount = DayCount.ACT_ACT_ICMA,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    frequency: int = 2,
) -> float:
    """Accrual fraction between `start` and `end`.

    For ACT/ACT-ICMA the fraction is expressed relative to the *coupon
    period* it falls in, which is why `period_start`/`period_end` are
    required to get the market-correct answer. They default to the
    start/end pair, which is only right when the interval is exactly one
    coupon period.
    """
    if end < start:
        raise ValueError(f"end ({end}) precedes start ({start})")

    if convention is DayCount.ACT_365F:
        return (end - start).days / 365.0
    if convention is DayCount.ACT_360:
        return (end - start).days / 360.0
    if convention is DayCount.THIRTY_360:
        return _thirty_360_days(start, end) / 360.0

    # ACT/ACT-ICMA: accrued days over actual days in the reference period,
    # scaled by the coupon frequency.
    ps = period_start if period_start is not None else start
    pe = period_end if period_end is not None else end
    period_days = (pe - ps).days
    if period_days <= 0:
        raise ValueError(f"degenerate coupon period {ps} -> {pe}")
    return ((end - start).days / period_days) / frequency


def year_fraction(
    start: date, end: date, convention: DayCount = DayCount.ACT_ACT_ICMA
) -> float:
    """Calendar time in years, used for discounting exponents.

    Unlike `day_count_factor` this is a *total elapsed time* measure, so
    ACT/ACT-ICMA degenerates to ACT/365-ish behaviour here; discounting uses
    it only to place cash flows on the time axis.
    """
    if convention is DayCount.ACT_360:
        return (end - start).days / 360.0
    if convention is DayCount.THIRTY_360:
        return _thirty_360_days(start, end) / 360.0
    return (end - start).days / 365.0
