"""Dynamic bond taxation by fiscal residence and instrument class.

The distinction that actually matters for net-yield accuracy in Italy, and
which most retail calculators get wrong:

* **Redditi di capitale** (coupons, `scarto di emissione`) are taxed at
  source and **cannot** be offset by capital losses.
* **Redditi diversi** (the gain between your purchase price and the
  sale/redemption price) **can** be offset by a `zainetto fiscale` of
  carried-forward `minusvalenze`, for four years after the year they arose.

Conflating the two overstates net yield for anyone holding losses, which is
why `TaxEngine` tracks the two buckets separately.

Rates encoded here are simplified statutory models intended for planning,
not tax advice; `SOURCES` documents what each profile is based on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "InstrumentClass",
    "FiscalResidence",
    "IncomeKind",
    "TaxProfile",
    "TaxEngine",
    "PROFILES",
]


class InstrumentClass(str, Enum):
    """Tax-relevant instrument taxonomy."""

    GOV_DOMESTIC = "gov_domestic"          # BTP, BOT, CCT, CTZ
    GOV_WHITELIST = "gov_whitelist"        # Bund, OAT, Bonos, other white-list sovereigns
    GOV_NON_WHITELIST = "gov_non_whitelist"
    SUPRANATIONAL = "supranational"        # EIB, ESM, World Bank
    CORPORATE = "corporate"
    BANK = "bank"


class FiscalResidence(str, Enum):
    IT = "IT"
    DE = "DE"
    FR = "FR"
    ES = "ES"
    NONE = "NONE"  # gross / tax-exempt modelling


class IncomeKind(str, Enum):
    """TUIR-style bucket. Drives loss-offset eligibility."""

    CAPITAL_INCOME = "redditi_di_capitale"  # coupons, issue discount
    OTHER_INCOME = "redditi_diversi"        # trading capital gain


# White-list sovereigns get the reduced Italian rate (D.M. 4/9/1996 as amended).
WHITELIST_HINT = {
    "DE", "FR", "ES", "NL", "BE", "AT", "IE", "PT", "FI", "LU", "GR",
    "SK", "SI", "EE", "LV", "LT", "CY", "MT", "HR", "US", "GB", "JP",
    "CH", "CA", "AU", "NO", "SE", "DK", "PL", "CZ", "HU", "RO", "BG",
}


@dataclass(frozen=True)
class TaxProfile:
    """Tax treatment for one fiscal residence."""

    residence: FiscalResidence
    label: str
    # Rate applied to coupons / issue discount, by instrument class.
    coupon_rates: dict[InstrumentClass, float]
    # Rate applied to realised capital gains, by instrument class.
    capital_gain_rates: dict[InstrumentClass, float]
    # Annual stamp duty on custody market value (IT: imposta di bollo 0.2%).
    stamp_duty_rate: float = 0.0
    # Whether carried-forward losses may offset OTHER_INCOME.
    allows_loss_offset: bool = True
    # Years a realised loss stays usable.
    loss_carryforward_years: int = 4
    notes: str = ""

    def rate_for(self, kind: IncomeKind, cls: InstrumentClass) -> float:
        table = (
            self.coupon_rates
            if kind is IncomeKind.CAPITAL_INCOME
            else self.capital_gain_rates
        )
        if cls not in table:
            raise KeyError(
                f"{self.residence.value} profile has no rate for {cls.value}"
            )
        return table[cls]


_IT_REDUCED = 0.125
_IT_FULL = 0.26
_IT_COUPONS = {
    InstrumentClass.GOV_DOMESTIC: _IT_REDUCED,
    InstrumentClass.GOV_WHITELIST: _IT_REDUCED,
    InstrumentClass.GOV_NON_WHITELIST: _IT_FULL,
    InstrumentClass.SUPRANATIONAL: _IT_REDUCED,
    InstrumentClass.CORPORATE: _IT_FULL,
    InstrumentClass.BANK: _IT_FULL,
}

# Germany: Abgeltungsteuer 25% + 5.5% Solidaritätszuschlag on the tax = 26.375%.
_DE_FLAT = 0.25 * 1.055
# France: PFU / flat tax = 12.8% income tax + 17.2% prélèvements sociaux.
_FR_FLAT = 0.128 + 0.172
# Spain: base del ahorro is progressive (19-30%); 21% models a mid bracket.
_ES_FLAT = 0.21

PROFILES: dict[FiscalResidence, TaxProfile] = {
    FiscalResidence.IT: TaxProfile(
        residence=FiscalResidence.IT,
        label="Italia — imposta sostitutiva 12,5% / 26%",
        coupon_rates=dict(_IT_COUPONS),
        capital_gain_rates=dict(_IT_COUPONS),
        stamp_duty_rate=0.002,
        allows_loss_offset=True,
        loss_carryforward_years=4,
        notes=(
            "12,5% su titoli di Stato e white list, 26% su corporate e bancari. "
            "Le minusvalenze compensano solo i redditi diversi, non le cedole."
        ),
    ),
    FiscalResidence.DE: TaxProfile(
        residence=FiscalResidence.DE,
        label="Deutschland — Abgeltungsteuer 26,375%",
        coupon_rates={c: _DE_FLAT for c in InstrumentClass},
        capital_gain_rates={c: _DE_FLAT for c in InstrumentClass},
        allows_loss_offset=True,
        loss_carryforward_years=99,  # effectively unlimited for Kapitalerträge
        notes="25% + 5,5% Solidaritätszuschlag. Kirchensteuer not modelled.",
    ),
    FiscalResidence.FR: TaxProfile(
        residence=FiscalResidence.FR,
        label="France — PFU 30%",
        coupon_rates={c: _FR_FLAT for c in InstrumentClass},
        capital_gain_rates={c: _FR_FLAT for c in InstrumentClass},
        allows_loss_offset=True,
        loss_carryforward_years=10,
        notes="12,8% IR + 17,2% prélèvements sociaux. Barème option not modelled.",
    ),
    FiscalResidence.ES: TaxProfile(
        residence=FiscalResidence.ES,
        label="España — base del ahorro ~21%",
        coupon_rates={c: _ES_FLAT for c in InstrumentClass},
        capital_gain_rates={c: _ES_FLAT for c in InstrumentClass},
        allows_loss_offset=True,
        loss_carryforward_years=4,
        notes="Progressive 19-30% in reality; flat 21% models a mid bracket.",
    ),
    FiscalResidence.NONE: TaxProfile(
        residence=FiscalResidence.NONE,
        label="Gross (no tax)",
        coupon_rates={c: 0.0 for c in InstrumentClass},
        capital_gain_rates={c: 0.0 for c in InstrumentClass},
        allows_loss_offset=False,
        notes="Gross-yield baseline for comparison.",
    ),
}

SOURCES = {
    "IT": "D.Lgs. 239/1996; art. 44 e 67 TUIR; D.M. 4/9/1996 (white list).",
    "DE": "EStG §20, §32d Abgeltungsteuer + SolzG.",
    "FR": "CGI art. 200 A (PFU) + CSG/CRDS.",
    "ES": "Ley 35/2006 IRPF, base imponible del ahorro.",
}


@dataclass
class TaxEngine:
    """Applies a `TaxProfile`, optionally consuming a loss carryforward.

    `loss_carryforward` is the `zainetto fiscale`: realised losses available
    to offset future OTHER_INCOME. It is consumed by `tax_on`, so a single
    engine instance models a sequence of taxable events in order.
    """

    residence: FiscalResidence = FiscalResidence.IT
    loss_carryforward: float = 0.0
    _consumed: float = field(default=0.0, init=False)

    @property
    def profile(self) -> TaxProfile:
        return PROFILES[self.residence]

    def tax_on(
        self,
        amount: float,
        kind: IncomeKind,
        cls: InstrumentClass,
        *,
        use_carryforward: bool = True,
    ) -> float:
        """Tax due on `amount` of income. Negative amounts return 0 tax.

        A negative OTHER_INCOME amount (a realised loss) is *added* to the
        carryforward rather than generating a refund, which is how the
        `zainetto` actually behaves.
        """
        profile = self.profile
        if amount < 0:
            if kind is IncomeKind.OTHER_INCOME and profile.allows_loss_offset:
                self.loss_carryforward += -amount
            return 0.0

        taxable = amount
        if (
            kind is IncomeKind.OTHER_INCOME
            and use_carryforward
            and profile.allows_loss_offset
            and self.loss_carryforward > 0
        ):
            offset = min(self.loss_carryforward, taxable)
            taxable -= offset
            self.loss_carryforward -= offset
            self._consumed += offset

        return taxable * profile.rate_for(kind, cls)

    @property
    def carryforward_used(self) -> float:
        """Total losses consumed by this engine so far."""
        return self._consumed

    def stamp_duty(self, market_value: float, years: float) -> float:
        """Italian `imposta di bollo` style custody levy."""
        return market_value * self.profile.stamp_duty_rate * max(years, 0.0)


def classify(issuer_country: str, is_government: bool, is_supranational: bool = False) -> InstrumentClass:
    """Map an issuer to its Italian tax class."""
    if is_supranational:
        return InstrumentClass.SUPRANATIONAL
    cc = (issuer_country or "").upper()
    if is_government:
        if cc == "IT":
            return InstrumentClass.GOV_DOMESTIC
        return (
            InstrumentClass.GOV_WHITELIST
            if cc in WHITELIST_HINT
            else InstrumentClass.GOV_NON_WHITELIST
        )
    return InstrumentClass.CORPORATE
