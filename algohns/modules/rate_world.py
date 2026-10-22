"""Live sovereign-bond world engine for the Trading Simulation.

This is the "world" behind the Trading Simulation page. It models, for ~15
countries, a **live yield curve** at the 2y / 5y / 10y / 30y points, each moving
in real time under

* a per-tenor mean-reverting process (Vasicek / Ornstein-Uhlenbeck, the family
  used in :mod:`algohns.modules.stochastic`) with a **shared risk factor** so
  markets move together, loaded through each country's ``beta_riskoff`` (safe
  havens rally on risk-off, risky issuers sell off), and
* a geopolitical **event** generator (Poisson arrivals) whose shocks have both a
  *scope* (global / regional / single-country) and a **curve shape**: central-bank
  surprises hit the front end, fiscal and supply shocks hit the long end, and
  flight-to-quality shifts the whole curve. That is what makes a single headline
  steepen or flatten curves and blow out peripheral spreads all at once.

Two layers, deliberately separate:

* **Pure logic** (``price_from_yield``, ``CountryState``, ``RateWorld.tick``) is
  deterministic given an RNG and thread-free, so it is unit-testable without
  Streamlit or wall-clock timing.
* **Live layer** (``RateWorld.start`` / ``snapshot``) runs the pure ``tick`` on a
  background daemon thread so prices keep moving while the Streamlit UI merely
  reads a locked snapshot. The page keeps the world in ``st.cache_resource`` so
  it survives Streamlit's per-interaction re-runs — exactly one world per session.

Everything here is a **simulation for practice**: levels are indicative model
assumptions, not live market data, and nothing trades real money.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

# ---------------------------------------------------------------------------
# Bond maths (self-contained so the tick loop stays fast and dependency-free)
# ---------------------------------------------------------------------------
# The platform's full BondEngine (algohns.modules.bond_engine) does ICMA accrual,
# multi-tax cash-flows and a QuantLib cross-check — too slow to re-run for ~60
# instruments several times a second. For a live mid we only need a clean price
# from a yield, so a textbook present value closes the loop here.


def price_from_yield(
    coupon_rate: float, ttm_years: float, ytm: float,
    freq: int = 1, face: float = 100.0,
) -> float:
    """Clean price per 100 face of a plain-vanilla bond at yield ``ytm``.

    Standard discounted cash-flow: ``freq`` coupons a year of ``face*coupon/freq``
    plus ``face`` at maturity, discounted at ``ytm/freq`` per period. Handles
    negative yields (post-2014 Europe) and the ~zero-yield limit.
    """
    n = max(1, int(round(ttm_years * freq)))
    cpn = face * coupon_rate / freq
    r = ytm / freq
    if abs(r) < 1e-12:                       # flat-rate limit, avoid 0/0
        return cpn * n + face
    disc = (1.0 + r) ** (-n)
    annuity = (1.0 - disc) / r
    return cpn * annuity + face * disc


def price_dv01(
    coupon_rate: float, ttm_years: float, ytm: float,
    freq: int = 1, face: float = 100.0,
) -> float:
    """Price change (points per 100 face) for a **1bp fall** in yield.

    Central difference around ``ytm``; positive because price rises as yield
    falls. Multiply by ``notional/100`` for the money DV01 of a position.
    """
    up = price_from_yield(coupon_rate, ttm_years, ytm + 1e-4, freq, face)
    dn = price_from_yield(coupon_rate, ttm_years, ytm - 1e-4, freq, face)
    return (dn - up) / 2.0


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------
TENORS: tuple[int, ...] = (2, 5, 10, 30)   # curve points, in years

#: per-tenor weight of a market's idiosyncratic jitter (long end a touch noisier)
_IDIO_W = {2: 0.80, 5: 0.90, 10: 1.00, 30: 1.05}
#: per-tenor loading on the shared risk factor (flight-to-quality bites the long end)
_RISKOFF_W = {2: 0.75, 5: 0.90, 10: 1.00, 30: 1.05}


@dataclass(frozen=True)
class CountrySpec:
    """Static description of one sovereign curve.

    ``beta_riskoff`` is the loading on the global risk factor: safe havens are
    **negative** (yields fall in a panic, a flight to quality), risky issuers
    **positive**. The base curve is pinned by its 2y and 10y levels plus a 30y
    term premium (``long_tp``); the 5y is interpolated.
    """

    code: str
    name: str
    flag: str
    region: str
    rating: str
    short: float            # base 2y yield
    ten: float              # base 10y yield
    long_tp: float          # 30y base = ten + long_tp
    freq: int               # coupons per year (EU annual, US/UK/JP semi-annual)
    beta_riskoff: float     # risk-off loading (+ risky, - safe haven)
    tick_vol_bp: float      # idiosyncratic per-tick yield std-dev, in bp
    kappa: float = 0.06     # pull-back strength toward base per tick

    def base_curve(self) -> dict[int, float]:
        """Base yield for each tenor."""
        return {
            2: self.short,
            5: self.short + (self.ten - self.short) * 0.6,
            10: self.ten,
            30: self.ten + self.long_tp,
        }


# ~15 sovereigns across six contagion clusters. Levels are plausible but
# indicative — this is a trainer, not a data feed.
UNIVERSE: tuple[CountrySpec, ...] = (
    # ---- Core euro (the safe side of the single currency) ----
    CountrySpec("DE", "Germany (Bund)",      "🇩🇪", "Core EU",      "AAA", 2.00, 2.55, 0.45, 1, -1.00, 0.18),
    CountrySpec("NL", "Netherlands (DSL)",   "🇳🇱", "Core EU",      "AAA", 2.20, 2.78, 0.45, 1, -0.75, 0.20),
    CountrySpec("FR", "France (OAT)",        "🇫🇷", "Core EU",      "AA-", 2.50, 3.25, 0.55, 1, -0.10, 0.24),
    # ---- Euro periphery (the contagion cluster) ----
    CountrySpec("IT", "Italy (BTP)",         "🇮🇹", "Periphery EU", "BBB", 2.80, 3.55, 0.75, 1,  1.15, 0.34),
    CountrySpec("ES", "Spain (Bono)",        "🇪🇸", "Periphery EU", "A",   2.60, 3.15, 0.60, 1,  0.85, 0.30),
    CountrySpec("PT", "Portugal (OT)",       "🇵🇹", "Periphery EU", "A-",  2.50, 3.00, 0.55, 1,  0.95, 0.30),
    CountrySpec("GR", "Greece (GGB)",        "🇬🇷", "Periphery EU", "BBB", 2.80, 3.35, 0.70, 1,  1.30, 0.38),
    # ---- Anglosphere ----
    CountrySpec("US", "US Treasury",         "🇺🇸", "Anglo",        "AA+", 3.90, 4.15, 0.35, 2, -0.65, 0.22),
    CountrySpec("GB", "UK Gilt",             "🇬🇧", "Anglo",        "AA",  4.20, 4.55, 0.40, 2,  0.15, 0.28),
    # ---- Japan ----
    CountrySpec("JP", "Japan (JGB)",         "🇯🇵", "Japan",        "A+",  0.70, 1.62, 0.70, 2, -0.85, 0.16),
    # ---- LatAm EM ----
    CountrySpec("BR", "Brazil (NTN-F)",      "🇧🇷", "LatAm EM",     "BB",  11.2, 11.6, 0.30, 2,  1.45, 0.95),
    CountrySpec("MX", "Mexico (Mbono)",      "🇲🇽", "LatAm EM",     "BBB", 9.30, 9.10, -0.10, 2,  1.10, 0.80),
    # ---- EMEA EM ----
    CountrySpec("ZA", "South Africa (R)",    "🇿🇦", "EMEA EM",      "BB",  8.50, 9.55, 1.20, 2,  1.50, 0.95),
    CountrySpec("TR", "Türkiye (CPI-linked)","🇹🇷", "EMEA EM",      "B",   30.0, 28.0, -1.50, 2,  2.00, 2.20),
    CountrySpec("PL", "Poland (POLGB)",      "🇵🇱", "EMEA EM",      "A-",  5.00, 5.55, 0.50, 1,  1.00, 0.45),
)


# ---------------------------------------------------------------------------
# Geopolitical event catalogue (rates-native, tagged by curve shape)
# ---------------------------------------------------------------------------
# Curve shapes: how a shock distributes across the four tenors. Policy surprises
# live at the front, fiscal/supply at the long end, flight-to-quality is parallel.
CURVE_SHAPES: dict[str, dict[int, float]] = {
    "parallel": {2: 1.00, 5: 1.00, 10: 1.00, 30: 1.00},
    "short":    {2: 1.00, 5: 0.65, 10: 0.30, 30: 0.12},
    "long":     {2: 0.12, 5: 0.35, 10: 0.80, 30: 1.00},
}


@dataclass(frozen=True)
class EventTemplate:
    """A geopolitical headline template and the rate shock it carries.

    ``bp`` is the size of the impulse in basis points (sign included: positive =
    yields up, negative = yields down). ``curve`` selects the shape across
    tenors. ``scale_by_beta`` means the impulse is a *risk* shock routed through
    each country's ``beta_riskoff`` (so havens and risky names move oppositely);
    when false it is a direct rate move (a policy or fiscal shift) with only a
    mild credit tilt. ``scope`` decides who is hit; a country shock splashes onto
    regional peers through ``spill``.
    """

    scope: str              # "global" | "region" | "country"
    category: str
    curve: str              # key into CURVE_SHAPES
    bp: float               # impulse in bp (sign = direction)
    scale_by_beta: bool     # route through beta_riskoff (a risk shock)?
    headline: str           # {t} filled with the target's name
    spill: float = 0.35     # fraction of a country shock onto its region


EVENT_CATALOGUE: tuple[EventTemplate, ...] = (
    # ---- global risk regime (parallel, risk-routed) ----
    EventTemplate("global", "Geopolitics", "parallel",  48, True,  "Military escalation sparks a global flight to quality"),
    EventTemplate("global", "Risk",        "parallel", -22, True,  "Risk-on melt-up as global earnings beat"),
    EventTemplate("global", "Energy",      "parallel",  30, True,  "Oil spikes on a supply shock, inflation fears return"),
    EventTemplate("global", "Liquidity",   "parallel",  40, True,  "Funding stress hits money markets, a dash for cash"),
    EventTemplate("global", "Growth",      "parallel",  26, True,  "Global PMIs roll over, recession signals cluster"),
    # ---- global policy / inflation (front end, direct) ----
    EventTemplate("global", "Central Bank","short",    -30, False, "Major central banks signal faster, coordinated rate cuts"),
    EventTemplate("global", "Inflation",   "short",     34, False, "Hot global CPI prints reprice front-end rates higher"),
    EventTemplate("global", "Central Bank","short",     26, False, "Hawkish hold: policymakers push back on cut bets"),
    # ---- global supply / duration (long end, direct) ----
    EventTemplate("global", "Supply",      "long",      22, False, "Heavy sovereign issuance calendar pressures the long end"),
    EventTemplate("global", "Duration",    "long",     -18, False, "Pension and insurer demand bids the long end"),
    # ---- regional ----
    EventTemplate("region", "Politics",    "parallel",  34, True,  "Political turmoil spreads across {t}"),
    EventTemplate("region", "Central Bank","short",    -24, False, "{t} central banks turn dovish in a coordinated move"),
    EventTemplate("region", "Central Bank","short",     24, False, "{t} policymakers turn hawkish on sticky inflation"),
    EventTemplate("region", "Growth",      "parallel",  22, True,  "Recession signals cluster across {t}"),
    EventTemplate("region", "Flows",       "parallel", -18, True,  "Foreign inflows chase {t} carry, yields grind lower"),
    EventTemplate("region", "Fiscal",      "long",      20, False, "Coordinated fiscal expansion steepens {t} curves"),
    # ---- single country (credit / idiosyncratic) ----
    EventTemplate("country", "Rating",     "parallel",  42, True,  "{t} downgraded by a major rating agency"),
    EventTemplate("country", "Rating",     "parallel", -28, True,  "{t} upgraded, spread compresses sharply"),
    EventTemplate("country", "Politics",   "parallel",  38, True,  "Snap election called in {t}, budget in doubt"),
    EventTemplate("country", "Fiscal",     "long",      34, False, "{t} unveils a larger-than-expected deficit, long end sells off"),
    EventTemplate("country", "Auction",    "long",      20, False, "Weak {t} bond auction, a tail at the long end"),
    EventTemplate("country", "Auction",    "long",     -15, False, "Strong {t} auction, heavy bid-to-cover"),
    EventTemplate("country", "Reform",     "parallel", -24, True,  "{t} passes credible reform, outlook raised to positive"),
    EventTemplate("country", "Central Bank","short",    28, False, "{t} delivers a surprise hike to defend the currency"),
    EventTemplate("country", "Central Bank","short",   -26, False, "{t} cuts rates more than expected"),
)

REGIONS: tuple[str, ...] = (
    "Core EU", "Periphery EU", "Anglo", "Japan", "LatAm EM", "EMEA EM",
)


# ---------------------------------------------------------------------------
# Live state
# ---------------------------------------------------------------------------
@dataclass
class CountryState:
    """Mutable live state for one country's whole curve, advanced each tick."""

    spec: CountrySpec
    yld: dict[int, float]                    # current yield per tenor
    prev: dict[int, float]                   # one tick ago (for up/down tint)
    open: dict[int, float]                   # at session open (session change)
    base: dict[int, float]                   # base/long-run curve
    coupon: dict[int, float]                 # fixed coupon per benchmark
    pressure: dict[int, float] = field(default_factory=dict)
    price: dict[int, float] = field(default_factory=dict)
    dv01: dict[int, float] = field(default_factory=dict)
    hist: dict[int, deque] = field(default_factory=dict)

    @classmethod
    def build(cls, spec: CountrySpec) -> "CountryState":
        base = spec.base_curve()
        coupon = {t: round(base[t], 2) for t in TENORS}
        cs = cls(
            spec=spec,
            yld=dict(base), prev=dict(base), open=dict(base), base=dict(base),
            coupon=coupon,
            pressure={t: 0.0 for t in TENORS},
            price={t: 0.0 for t in TENORS},
            dv01={t: 0.0 for t in TENORS},
            hist={t: deque(maxlen=240) for t in TENORS},
        )
        cs.refresh_prices()
        for t in TENORS:
            cs.hist[t].append(cs.price[t])
        return cs

    def refresh_prices(self) -> None:
        # Yields and coupons are stored in PERCENT (3.55 = 3.55%); the pricing
        # helpers want decimal fractions, so convert on the way in.
        s = self.spec
        for t in TENORS:
            y = self.yld[t] / 100.0
            c = self.coupon[t] / 100.0
            self.price[t] = price_from_yield(c, t, y, s.freq)
            self.dv01[t] = price_dv01(c, t, y, s.freq)


@dataclass
class LiveEvent:
    """A fired headline, kept for the scrolling ticker."""

    ts: float
    category: str
    scope: str
    curve: str
    text: str
    bp: float               # signed impulse size (for colour / magnitude)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class RateWorld:
    """A live, self-advancing sovereign-curve market.

    Construct with a ``seed`` and a ``difficulty`` that scales both volatility
    and event frequency. Call :meth:`tick` to advance one step (pure, testable),
    or :meth:`start` to let a background thread tick it and read :meth:`snapshot`.
    """

    #: (vol multiplier, event-rate multiplier) per difficulty label
    DIFFICULTY = {
        "Calmo":    (0.5, 0.35),
        "Normale":  (1.0, 1.00),
        "Volatile": (2.0, 2.20),
    }

    def __init__(
        self, seed: int = 0, difficulty: str = "Normale",
        tick_dt: float = 0.15,
    ) -> None:
        self.rng = np.random.default_rng(seed)
        self.difficulty = difficulty
        self.tick_dt = tick_dt              # wall-clock seconds between ticks
        self.states: dict[str, CountryState] = {
            spec.code: CountryState.build(spec) for spec in UNIVERSE
        }
        self.events: deque[LiveEvent] = deque(maxlen=60)
        self.ticks = 0
        self.started_at = time.time()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.paused = False

    # ---------------------------------------------------------------- config
    def set_difficulty(self, difficulty: str) -> None:
        if difficulty in self.DIFFICULTY:
            with self._lock:
                self.difficulty = difficulty

    # ------------------------------------------------------------ pure step
    def tick(self) -> None:
        """Advance the whole world one step. Pure given ``self.rng``.

        A single shared ``common`` draw correlates everyone this tick; each
        country loads it through ``beta_riskoff`` and each tenor through
        ``_RISKOFF_W``. On top sit per-tenor idiosyncratic noise, mean reversion
        to the base curve plus any decaying event pressure.
        """
        vol_mult, ev_mult = self.DIFFICULTY.get(self.difficulty, (1.0, 1.0))
        with self._lock:
            common = self.rng.standard_normal() * 0.6 * vol_mult   # bp, shared
            for cs in self.states.values():
                s = cs.spec
                for t in TENORS:
                    cs.prev[t] = cs.yld[t]
                    idio = (self.rng.standard_normal()
                            * s.tick_vol_bp * vol_mult * _IDIO_W[t])
                    shock_bp = idio + s.beta_riskoff * common * _RISKOFF_W[t]
                    target = cs.base[t] + cs.pressure[t]
                    cs.yld[t] += (target - cs.yld[t]) * s.kappa + shock_bp / 100.0
                    cs.pressure[t] *= 0.97     # event pressure fades each tick
                cs.refresh_prices()
                for t in TENORS:
                    cs.hist[t].append(cs.price[t])
            base_rate_per_tick = 0.012 * ev_mult   # ~1 event / 12s at Normale
            if self.rng.random() < base_rate_per_tick:
                self._fire_event()
            self.ticks += 1

    def _fire_event(self) -> None:
        """Pick a template, resolve its target, apply the curve shock. Locked."""
        tpl = EVENT_CATALOGUE[int(self.rng.integers(len(EVENT_CATALOGUE)))]
        mag = tpl.bp * (0.7 + 0.6 * self.rng.random())     # ±30% size jitter
        shape = CURVE_SHAPES[tpl.curve]

        if tpl.scope == "global":
            affected = {c: 1.0 for c in self.states}
            label = "Global"
        elif tpl.scope == "region":
            region = str(self.rng.choice(REGIONS))
            affected = {c: 1.0 for c, st in self.states.items()
                        if st.spec.region == region}
            label = region
        else:  # country — splashes onto regional peers through `spill`
            code = str(self.rng.choice(list(self.states)))
            region = self.states[code].spec.region
            affected = {code: 1.0}
            for c, st in self.states.items():
                if c != code and st.spec.region == region:
                    affected[c] = tpl.spill
            label = self.states[code].spec.name

        for code, w in affected.items():
            cs = self.states[code]
            for t in TENORS:
                if tpl.scale_by_beta:
                    bp = cs.spec.beta_riskoff * mag * w * shape[t]
                else:
                    tilt = 1.0 + 0.15 * cs.spec.beta_riskoff   # risky react more
                    bp = mag * w * shape[t] * tilt
                cs.yld[t] += (bp / 100.0) * 0.5         # half instant jump…
                cs.pressure[t] += (bp / 100.0) * 0.5    # …half fading pressure
            cs.refresh_prices()

        self.events.appendleft(LiveEvent(
            ts=time.time(), category=tpl.category, scope=tpl.scope,
            curve=tpl.curve, text=tpl.headline.format(t=label), bp=mag,
        ))

    # ------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        """A consistent, lock-safe copy of the world for the UI to render.

        Each row carries the 10y headline (yield, price, changes, Bund spread)
        plus the 2s10s slope and a full ``curve`` dict keyed by tenor.
        """
        with self._lock:
            de = self.states["DE"].yld
            rows = []
            for cs in self.states.values():
                s = cs.spec
                curve = {}
                for t in TENORS:
                    curve[t] = {
                        "yield": cs.yld[t],
                        "price": cs.price[t],
                        "dv01": cs.dv01[t],
                        "chg_session_bp": (cs.yld[t] - cs.open[t]) * 100.0,
                        "chg_tick_bp": (cs.yld[t] - cs.prev[t]) * 100.0,
                        "spread_vs_bund_bp": (cs.yld[t] - de[t]) * 100.0,
                    }
                rows.append({
                    "code": s.code, "name": s.name, "flag": s.flag,
                    "region": s.region, "rating": s.rating, "freq": s.freq,
                    "yield": cs.yld[10], "price": cs.price[10],
                    "dv01": cs.dv01[10],
                    "chg_tick_bp": (cs.yld[10] - cs.prev[10]) * 100.0,
                    "chg_session_bp": (cs.yld[10] - cs.open[10]) * 100.0,
                    "spread_vs_bund_bp": (cs.yld[10] - de[10]) * 100.0,
                    "slope_2s10s_bp": (cs.yld[10] - cs.yld[2]) * 100.0,
                    "curve": curve,
                })
            events = [{"ts": e.ts, "category": e.category, "scope": e.scope,
                       "curve": e.curve, "text": e.text, "bp": e.bp}
                      for e in self.events]
            return {
                "rows": rows, "events": events, "ticks": self.ticks,
                "elapsed": time.time() - self.started_at,
                "difficulty": self.difficulty, "tenors": list(TENORS),
            }

    def instrument(self, code: str, tenor: int) -> dict:
        """Live view of one curve point for the RFQ desk."""
        with self._lock:
            cs = self.states[code]
            s = cs.spec
            de = self.states["DE"].yld[tenor]
            return {
                "code": f"{code}_{tenor}y", "country": code, "tenor": tenor,
                "name": s.name, "display": f"{s.name} {tenor}y",
                "flag": s.flag, "region": s.region, "rating": s.rating,
                "freq": s.freq, "coupon": cs.coupon[tenor],
                "yield": cs.yld[tenor], "price": cs.price[tenor],
                "dv01": cs.dv01[tenor],
                "spread_vs_bund_bp": (cs.yld[tenor] - de) * 100.0,
                "history": list(cs.hist[tenor]),
            }

    def marks(self) -> dict[str, float]:
        """Current mid price for every instrument code (``CC_Ny``)."""
        with self._lock:
            out = {}
            for code, cs in self.states.items():
                for t in TENORS:
                    out[f"{code}_{t}y"] = cs.price[t]
            return out

    # --------------------------------------------------------- live thread
    def start(self) -> "RateWorld":
        """Start the background tick loop (idempotent)."""
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="rate-world")
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.paused:
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 - a bad tick must not kill the world
                    pass
            time.sleep(self.tick_dt)

    def stop(self) -> None:
        self._stop.set()


__all__ = [
    "price_from_yield", "price_dv01", "CountrySpec", "UNIVERSE", "TENORS",
    "CURVE_SHAPES", "EventTemplate", "EVENT_CATALOGUE", "REGIONS",
    "CountryState", "LiveEvent", "RateWorld",
]
