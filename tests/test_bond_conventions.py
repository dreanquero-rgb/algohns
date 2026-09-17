"""Bond-maths conventions, pinned.

These exist because the engine previously discounted on a calendar ACT/365
axis with `(1 + y)^t` compounding, which put a 4% semi-annual par bond at
4.0381% and a 5% quarterly one at 5.0924% — a few basis points that would
disagree with every terminal and, on a screener, misrank bonds.

The par-bond identity is the cheapest possible regression test for both
conventions at once, so it is worth pinning tightly.
"""
from __future__ import annotations

from datetime import date

import pytest

from algohns.modules.bond_engine import Bond, BondEngine


def _bond(coupon: float, freq: int, price: float = 100.0, **kw) -> Bond:
    base = dict(
        face_value=100.0, coupon_rate=coupon, frequency=freq,
        issue_date=date(2020, 1, 1), maturity_date=date(2030, 1, 1),
        settlement_date=date(2025, 1, 1), clean_price=price,
        isin="TEST", name="test",
    )
    base.update(kw)
    return Bond(**base)


@pytest.fixture(scope="module")
def engine() -> BondEngine:
    return BondEngine()


class TestParIdentity:
    """A bond at par on a coupon date yields exactly its coupon rate."""

    @pytest.mark.parametrize("coupon,freq", [
        (0.02, 1), (0.03, 1), (0.04, 2), (0.06, 2), (0.10, 2),
        (0.05, 4), (0.035, 4),
    ])
    def test_par_bond_yields_its_coupon(self, engine, coupon, freq):
        a = engine.analyse(_bond(coupon, freq), "GROSS")
        assert a.ytm_gross == pytest.approx(coupon, abs=1e-7)

    def test_no_accrued_on_a_coupon_date(self, engine):
        a = engine.analyse(_bond(0.04, 2), "GROSS")
        assert a.accrued_interest == pytest.approx(0.0, abs=1e-9)


class TestPriceYieldRelation:
    def test_discount_yields_above_coupon(self, engine):
        assert engine.analyse(_bond(0.04, 2, 95.0), "GROSS").ytm_gross > 0.04

    def test_premium_yields_below_coupon(self, engine):
        assert engine.analyse(_bond(0.04, 2, 105.0), "GROSS").ytm_gross < 0.04

    def test_duration_is_positive_and_under_maturity(self, engine):
        a = engine.analyse(_bond(0.04, 2), "GROSS")
        assert 0 < a.modified_duration < 5.0
        assert a.convexity > 0

    def test_longer_maturity_carries_more_duration(self, engine):
        short = engine.analyse(_bond(0.04, 2, maturity_date=date(2028, 1, 1)), "GROSS")
        long_ = engine.analyse(_bond(0.04, 2, maturity_date=date(2045, 1, 1)), "GROSS")
        assert long_.modified_duration > short.modified_duration
        assert long_.convexity > short.convexity

    def test_zero_coupon_duration_equals_maturity(self, engine):
        """A zero's Macaulay duration is its time to maturity."""
        a = engine.analyse(_bond(0.0, 1, 82.0), "GROSS")
        assert a.macaulay_duration == pytest.approx(5.0, abs=0.02)


class TestTaxation:
    def test_net_never_exceeds_gross(self, engine):
        for price in (90.0, 95.0, 100.0, 105.0, 110.0):
            a = engine.analyse(_bond(0.04, 2, price), "IT_GOV_WHITELIST")
            assert a.ytm_net <= a.ytm_gross + 1e-12, price

    def test_gross_profile_has_no_tax(self, engine):
        a = engine.analyse(_bond(0.04, 2, 97.0), "GROSS")
        assert a.ytm_net == pytest.approx(a.ytm_gross, abs=1e-9)

    def test_par_govvie_net_is_coupon_after_tax(self, engine):
        """At par there is no redemption gain, so the drag is the coupon haircut."""
        a = engine.analyse(_bond(0.04, 2), "IT_GOV_WHITELIST")
        assert a.ytm_net == pytest.approx(0.04 * 0.875, rel=2e-3)
