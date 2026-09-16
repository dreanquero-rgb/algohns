"""Tests for the bond analytics engine.

These pin the cases that have closed-form answers, so a regression in the
day-count or compounding convention fails loudly rather than drifting a few
basis points.
"""
from __future__ import annotations

import math
from datetime import date

import pytest

from core.taxation import FiscalResidence, InstrumentClass, classify
from modules.bond_engine import Bond, analyse, coupon_schedule

GROSS = FiscalResidence.NONE


def _bond(**kw):
    base = dict(
        isin="TEST", name="test", maturity=date(2030, 1, 1),
        coupon_rate=0.04, clean_price=100.0, frequency=2,
    )
    base.update(kw)
    return Bond(**base)


class TestParIdentity:
    """A bond priced at par on a coupon date yields exactly its coupon."""

    @pytest.mark.parametrize("coupon,freq", [
        (0.04, 2), (0.06, 2), (0.03, 1), (0.05, 4), (0.02, 12), (0.10, 2),
    ])
    def test_par_yields_coupon(self, coupon, freq):
        b = _bond(coupon_rate=coupon, clean_price=100.0, frequency=freq)
        a = analyse(b, date(2025, 1, 1), residence=GROSS)
        assert a.ytm_gross == pytest.approx(coupon, abs=1e-9)

    def test_accrued_zero_on_coupon_date(self):
        a = analyse(_bond(), date(2025, 1, 1), residence=GROSS)
        assert a.accrued_gross == pytest.approx(0.0, abs=1e-12)
        assert a.dirty_price == pytest.approx(a.bond.clean_price)


class TestZeroCoupon:
    def test_one_year_zc(self):
        b = _bond(maturity=date(2026, 1, 1), coupon_rate=0.0,
                  clean_price=96.0, frequency=1)
        a = analyse(b, date(2025, 1, 1), residence=GROSS)
        assert a.ytm_gross == pytest.approx(100.0 / 96.0 - 1.0, abs=1e-9)

    def test_zc_duration_equals_maturity(self):
        """A zero's Macaulay duration is its time to maturity."""
        b = _bond(maturity=date(2030, 1, 1), coupon_rate=0.0,
                  clean_price=82.0, frequency=1)
        a = analyse(b, date(2025, 1, 1), residence=GROSS)
        assert a.macaulay_duration == pytest.approx(5.0, abs=1e-6)

    def test_zc_has_no_accrued(self):
        b = _bond(coupon_rate=0.0, clean_price=90.0, frequency=1)
        a = analyse(b, date(2025, 4, 15), residence=GROSS)
        assert a.accrued_gross == 0.0


class TestPriceYieldRelation:
    def test_discount_bond_yields_above_coupon(self):
        a = analyse(_bond(clean_price=95.0), date(2025, 1, 1), residence=GROSS)
        assert a.ytm_gross > 0.04

    def test_premium_bond_yields_below_coupon(self):
        a = analyse(_bond(clean_price=105.0), date(2025, 1, 1), residence=GROSS)
        assert a.ytm_gross < 0.04

    def test_duration_positive_and_below_maturity(self):
        a = analyse(_bond(), date(2025, 1, 1), residence=GROSS)
        assert 0 < a.modified_duration < 5.0
        assert a.convexity > 0

    def test_longer_maturity_has_more_duration(self):
        short = analyse(_bond(maturity=date(2028, 1, 1)), date(2025, 1, 1), residence=GROSS)
        long_ = analyse(_bond(maturity=date(2045, 1, 1)), date(2025, 1, 1), residence=GROSS)
        assert long_.modified_duration > short.modified_duration
        assert long_.convexity > short.convexity

    def test_convexity_expansion_approximates_reprice(self):
        """Duration+convexity should track an exact reprice for a small shift."""
        b = _bond(clean_price=98.0, maturity=date(2035, 1, 1))
        a = analyse(b, date(2025, 1, 1), residence=GROSS)
        dy = 0.0010  # 10bp
        approx = a.price_change(dy)

        # Exact reprice at ytm+dy using the same period axis.
        f = b.frequency
        yp = (a.ytm_gross + dy) / f
        exact_px = sum(cf.gross / (1 + yp) ** (cf.years * f) for cf in a.cash_flows)
        exact = exact_px / a.dirty_price - 1.0
        assert approx == pytest.approx(exact, abs=2e-5)


class TestTaxation:
    def test_italian_govvie_uses_reduced_rate(self):
        b = _bond(clean_price=100.0, issuer_country="IT", is_government=True)
        a = analyse(b, date(2025, 1, 1), residence=FiscalResidence.IT)
        assert a.tax_class is InstrumentClass.GOV_DOMESTIC
        # At par there is no redemption gain, so drag is purely the coupon
        # haircut: net yield ~= gross * (1 - 0.125).
        assert a.ytm_net == pytest.approx(0.04 * 0.875, abs=1e-6)

    def test_corporate_taxed_at_26(self):
        b = _bond(clean_price=100.0, issuer_country="IT", is_government=False)
        a = analyse(b, date(2025, 1, 1), residence=FiscalResidence.IT)
        assert a.tax_class is InstrumentClass.CORPORATE
        assert a.ytm_net == pytest.approx(0.04 * 0.74, abs=1e-6)

    def test_net_never_exceeds_gross(self):
        for px in (90.0, 95.0, 100.0, 105.0, 110.0):
            a = analyse(_bond(clean_price=px), date(2025, 3, 10),
                        residence=FiscalResidence.IT)
            assert a.ytm_net <= a.ytm_gross + 1e-12, f"price {px}"

    def test_gross_residence_has_zero_drag(self):
        a = analyse(_bond(clean_price=97.0), date(2025, 6, 1), residence=GROSS)
        assert a.tax_drag_bps == pytest.approx(0.0, abs=1e-9)
        assert a.total_tax == pytest.approx(0.0)

    def test_loss_carryforward_reduces_redemption_tax(self):
        """A zainetto should lift net yield on a discount bond."""
        b = _bond(clean_price=90.0, issuer_country="IT")
        plain = analyse(b, date(2025, 1, 1), residence=FiscalResidence.IT)
        with_loss = analyse(b, date(2025, 1, 1), residence=FiscalResidence.IT,
                            loss_carryforward=50.0)
        assert with_loss.ytm_net > plain.ytm_net
        assert with_loss.loss_carryforward_used > 0

    def test_coupons_not_offsettable_by_losses(self):
        """Par bond: no redemption gain, so a zainetto must change nothing."""
        b = _bond(clean_price=100.0, issuer_country="IT")
        plain = analyse(b, date(2025, 1, 1), residence=FiscalResidence.IT)
        with_loss = analyse(b, date(2025, 1, 1), residence=FiscalResidence.IT,
                            loss_carryforward=1000.0)
        assert with_loss.ytm_net == pytest.approx(plain.ytm_net, abs=1e-12)
        assert with_loss.loss_carryforward_used == 0.0

    def test_whitelist_bund_gets_reduced_rate(self):
        bund = _bond(issuer_country="DE", is_government=True, clean_price=100.0)
        a = analyse(bund, date(2025, 1, 1), residence=FiscalResidence.IT)
        assert a.tax_class is InstrumentClass.GOV_WHITELIST
        assert a.ytm_net == pytest.approx(0.04 * 0.875, abs=1e-6)

    def test_non_whitelist_gets_full_rate(self):
        b = _bond(issuer_country="TR", is_government=True, clean_price=100.0)
        a = analyse(b, date(2025, 1, 1), residence=FiscalResidence.IT)
        assert a.tax_class is InstrumentClass.GOV_NON_WHITELIST
        assert a.ytm_net == pytest.approx(0.04 * 0.74, abs=1e-6)

    @pytest.mark.parametrize("res", [
        FiscalResidence.IT, FiscalResidence.DE, FiscalResidence.FR, FiscalResidence.ES,
    ])
    def test_all_residences_produce_finite_net_yield(self, res):
        a = analyse(_bond(clean_price=96.5), date(2025, 5, 20), residence=res)
        assert math.isfinite(a.ytm_net)
        assert a.ytm_net < a.ytm_gross

    def test_stamp_duty_lowers_net_yield(self):
        b = _bond(clean_price=99.0)
        without = analyse(b, date(2025, 1, 1), residence=FiscalResidence.IT)
        with_ = analyse(b, date(2025, 1, 1), residence=FiscalResidence.IT,
                        include_stamp_duty=True)
        assert with_.ytm_net < without.ytm_net
        assert with_.stamp_duty_total > 0


class TestSchedule:
    def test_schedule_ends_on_maturity(self):
        b = _bond(maturity=date(2030, 7, 1))
        s = coupon_schedule(b, date(2025, 1, 15))
        assert s[-1] == date(2030, 7, 1)
        assert all(d > date(2025, 1, 15) for d in s)
        assert s == sorted(s)

    def test_semiannual_count(self):
        b = _bond(maturity=date(2030, 1, 1), frequency=2)
        assert len(coupon_schedule(b, date(2025, 1, 1))) == 10

    def test_annual_count(self):
        b = _bond(maturity=date(2030, 1, 1), frequency=1)
        assert len(coupon_schedule(b, date(2025, 1, 1))) == 5

    def test_accrued_mid_period_is_half_coupon(self):
        """Settlement at the period midpoint accrues ~half a coupon."""
        b = _bond(maturity=date(2030, 1, 1), coupon_rate=0.04, frequency=2)
        # Jan 1 -> Jul 1 is 181 days in 2025; midpoint ~Apr 1 (90 days).
        a = analyse(b, date(2025, 4, 1), residence=GROSS)
        full_coupon = b.coupon_amount
        assert a.accrued_gross == pytest.approx(full_coupon * 90 / 181, rel=1e-6)


class TestValidation:
    def test_settlement_after_maturity_rejected(self):
        with pytest.raises(ValueError, match="past maturity"):
            analyse(_bond(maturity=date(2024, 1, 1)), date(2025, 1, 1))

    def test_bad_frequency_rejected(self):
        with pytest.raises(ValueError, match="frequency"):
            _bond(frequency=3)

    def test_nonpositive_price_rejected(self):
        with pytest.raises(ValueError, match="clean_price"):
            _bond(clean_price=0.0)

    def test_premium_bond_warns_about_minusvalenza(self):
        a = analyse(_bond(clean_price=108.0), date(2025, 1, 1),
                    residence=FiscalResidence.IT)
        assert any("minusvalenza" in w for w in a.warnings)


class TestClassify:
    @pytest.mark.parametrize("cc,gov,expected", [
        ("IT", True, InstrumentClass.GOV_DOMESTIC),
        ("DE", True, InstrumentClass.GOV_WHITELIST),
        ("FR", True, InstrumentClass.GOV_WHITELIST),
        ("TR", True, InstrumentClass.GOV_NON_WHITELIST),
        ("IT", False, InstrumentClass.CORPORATE),
        ("US", False, InstrumentClass.CORPORATE),
    ])
    def test_classification(self, cc, gov, expected):
        assert classify(cc, gov) is expected

    def test_supranational_wins(self):
        assert classify("LU", False, True) is InstrumentClass.SUPRANATIONAL
