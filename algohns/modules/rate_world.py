"""Live sovereign-bond world engine for the Trading Simulation.

This is the "world" behind the Trading Simulation page: a set of government
benchmark bonds whose yields move **in real time**, driven by

* a per-country mean-reverting short-rate process (Vasicek / Ornstein-Uhlenbeck,
  the same family used in :mod:`algohns.modules.stochastic`), so each market
  jiggles on its own, and
* a geopolitical **event** generator (Poisson arrivals) that fires risk-on /
  risk-off shocks with a *scope* (global, regional, single-country). Shocks hit
  every country through its ``beta_riskoff`` loading, so a single headline moves
  the whole board at once and correlated clusters (the EU periphery, the EM
  complex) move together — that is the contagion the user sees as spreads blow
  out.

Two layers, kept deliberately separate:

* **Pure logic** (``price_from_yield``, ``CountryState``, ``RateWorld.tick``) is
  deterministic given an RNG and has no threads, so it is unit-testable without
  Streamlit, numpy paths, or wall-clock timing.
* **Live layer** (``RateWorld.start`` / ``snapshot``) runs the pure ``tick`` on a
  background daemon thread so prices keep moving while the Streamlit UI merely
  *reads* a locked snapshot. Streamlit re-runs its script on every interaction;
  the world must not. Wrapping the engine in ``st.cache_resource`` on the page
  gives exactly one world per session that outlives those re-runs.

Everything here is a **simulation for practice** — levels are indicative model
assumptions, not live market data, and nothing trades real money.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

# ---------------------------------------------------------------------------
# Bond maths (self-contained so the tick loop stays fast and dependency-free)
# ---------------------------------------------------------------------------
# The platform's full BondEngine (algohns.modules.bond_engine) does ICMA accrual,
# multi-tax cash-flows and a QuantLib cross-check — overkill, and too slow, to
# re-run for ~15 bonds several times a second. For the live mid we only need a
# clean price from a yield, so a textbook present-value closes the loop here.


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
    """Price change (in points per 100 face) for a **1bp fall** in yield.

    Central difference around ``ytm``; positive because price rises as yield
    falls. Multiply by ``notional/100`` for the money DV01 of a position.
    """
    up = price_from_yield(coupon_rate, ttm_years, ytm + 1e-4, freq, face)
    dn = price_from_yield(coupon_rate, ttm_years, ytm - 1e-4, freq, face)
    return (dn - up) / 2.0


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CountrySpec:
    """Static description of one sovereign 10y benchmark.

    ``beta_riskoff`` is the loading on the global risk factor: safe havens are
    **negative** (yields fall when the world panics, a flight to quality),
    risky issuers are **positive** (yields spike). ``tick_vol_bp`` is the
    idiosyncratic per-tick yield jitter; EM markets are noisier than the core.
    """

    code: str
    name: str
    flag: str
    region: str
    rating: str
    coupon: float           # fixed annual coupon rate (set near par)
    base_yield: float       # starting / long-run 10y yield level
    freq: int               # coupons per year (EU annual, US/UK/JP semi-annual)
    beta_riskoff: float     # risk-off loading (+ risky, - safe haven)
    tick_vol_bp: float      # idiosyncratic per-tick yield std-dev, in bp
    kappa: float = 0.08     # pull-back strength toward base_yield per tick


# ~15 sovereigns across six contagion clusters. Yields are plausible but
# indicative — this is a trainer, not a data feed. Coupons sit near each
# starting yield so prices open close to par.
UNIVERSE: tuple[CountrySpec, ...] = (
    # ---- Core euro (the safe side of the single currency) ----
    CountrySpec("DE", "Germany (Bund)",      "🇩🇪", "Core EU",      "AAA", 2.50, 2.55, 1, -1.00, 0.18),
    CountrySpec("NL", "Netherlands (DSL)",   "🇳🇱", "Core EU",      "AAA", 2.70, 2.78, 1, -0.75, 0.20),
    CountrySpec("FR", "France (OAT)",        "🇫🇷", "Core EU",      "AA-", 3.10, 3.25, 1, -0.10, 0.24),
    # ---- Euro periphery (the contagion cluster) ----
    CountrySpec("IT", "Italy (BTP)",         "🇮🇹", "Periphery EU", "BBB", 3.50, 3.55, 1,  1.15, 0.34),
    CountrySpec("ES", "Spain (Bono)",        "🇪🇸", "Periphery EU", "A",   3.10, 3.15, 1,  0.85, 0.30),
    CountrySpec("PT", "Portugal (OT)",       "🇵🇹", "Periphery EU", "A-",  2.95, 3.00, 1,  0.95, 0.30),
    CountrySpec("GR", "Greece (GGB)",        "🇬🇷", "Periphery EU", "BBB", 3.30, 3.35, 1,  1.30, 0.38),
    # ---- Anglosphere ----
    CountrySpec("US", "US Treasury",         "🇺🇸", "Anglo",        "AA+", 4.00, 4.15, 2, -0.65, 0.22),
    CountrySpec("GB", "UK Gilt",             "🇬🇧", "Anglo",        "AA",  4.40, 4.55, 2,  0.15, 0.28),
    # ---- Japan ----
    CountrySpec("JP", "Japan (JGB)",         "🇯🇵", "Japan",        "A+",  1.50, 1.62, 2, -0.85, 0.16),
    # ---- LatAm EM ----
    CountrySpec("BR", "Brazil (NTN-F)",      "🇧🇷", "LatAm EM",     "BB",  11.0, 11.6, 2,  1.45, 0.95),
    CountrySpec("MX", "Mexico (Mbono)",      "🇲🇽", "LatAm EM",     "BBB", 9.00, 9.10, 2,  1.10, 0.80),
    # ---- EMEA EM ----
    CountrySpec("ZA", "South Africa (R)",    "🇿🇦", "EMEA EM",      "BB",  9.30, 9.55, 2,  1.50, 0.95),
    CountrySpec("TR", "Türkiye (CPI-linked)","🇹🇷", "EMEA EM",      "B",   27.0, 28.0, 2,  2.00, 2.20),
    CountrySpec("PL", "Poland (POLGB)",      "🇵🇱", "EMEA EM",      "A-",  5.40, 5.55, 1,  1.00, 0.45),
)

TTM_YEARS = 10.0   # every benchmark modelled as a fresh 10y for simplicity


# ---------------------------------------------------------------------------
# Geopolitical event catalogue
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EventTemplate:
    """A geopolitical headline template and the risk shock it carries.

    ``risk`` is the size of the global risk-off impulse in bp of yield for a
    unit ``beta_riskoff`` market (so a +40 risk-off event moves a beta -1 safe
    haven down ~40bp and a beta +1.3 periphery name up ~52bp). A **negative**
    ``risk`` is risk-on (yields of risky issuers fall, spreads compress).
    ``scope`` decides who is hit: everyone, one region's cluster, or a single
    country (which also splashes onto its region through ``spill``).
    """

    scope: str              # "global" | "region" | "country"
    category: str
    risk: float             # bp of risk-off impulse (negative = risk-on)
    headline: str           # {t} is filled with the target's name
    spill: float = 0.35     # fraction of a country shock that hits its region


EVENT_CATALOGUE: tuple[EventTemplate, ...] = (
    # ---- global risk regime ----
    EventTemplate("global", "Rates",      -22, "Fed signals faster rate cuts, global duration rallies"),
    EventTemplate("global", "Rates",       28, "Hot US CPI print reprices the curve higher"),
    EventTemplate("global", "Geopolitics", 45, "Military escalation in a key region sparks flight to quality"),
    EventTemplate("global", "Risk",       -18, "Risk-on melt-up as earnings beat across the board"),
    EventTemplate("global", "Energy",      30, "Oil spikes on supply shock, inflation fears return"),
    EventTemplate("global", "Liquidity",   38, "Funding stress hits money markets, dash for cash"),
    # ---- regional ----
    EventTemplate("region", "Politics",    32, "Political turmoil spreads across {t}"),
    EventTemplate("region", "Central Bank",-20, "{t} central banks turn dovish in a coordinated move"),
    EventTemplate("region", "Growth",      24, "Recession signals cluster across {t}"),
    EventTemplate("region", "Flows",      -16, "Foreign inflows chase {t} carry, yields grind lower"),
    # ---- single country ----
    EventTemplate("country", "Rating",     40, "{t} downgraded by a major rating agency"),
    EventTemplate("country", "Rating",    -26, "{t} upgraded, spread compresses sharply"),
    EventTemplate("country", "Politics",   36, "Snap election called in {t}, budget in doubt"),
    EventTemplate("country", "Fiscal",     30, "{t} unveils a larger-than-expected deficit"),
    EventTemplate("country", "Auction",    18, "Weak {t} bond auction, tail at the long end"),
    EventTemplate("country", "Auction",   -14, "Strong {t} auction, heavy bid-to-cover"),
    EventTemplate("country", "Reform",    -22, "{t} passes credible reform package, rating outlook raised"),
)

REGIONS: tuple[str, ...] = (
    "Core EU", "Periphery EU", "Anglo", "Japan", "LatAm EM", "EMEA EM",
)


# ---------------------------------------------------------------------------
# Live state
# ---------------------------------------------------------------------------
@dataclass
class CountryState:
    """Mutable live state for one country, advanced every tick."""

    spec: CountrySpec
    yld: float                              # current yield
    prev_yld: float                         # yield one tick ago (for up/down tint)
    open_yld: float                         # yield at session open (for session chg)
    pressure: float = 0.0                   # decaying event drift added to the OU target
    price: float = 0.0
    dv01: float = 0.0
    history: deque = field(default_factory=lambda: deque(maxlen=240))

    def refresh_price(self) -> None:
        s = self.spec
        self.price = price_from_yield(s.coupon, TTM_YEARS, self.yld, s.freq)
        self.dv01 = price_dv01(s.coupon, TTM_YEARS, self.yld, s.freq)


@dataclass
class LiveEvent:
    """A fired headline, kept for the scrolling ticker."""

    ts: float
    category: str
    scope: str
    text: str
    risk: float


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class RateWorld:
    """A live, self-advancing sovereign-bond market.

    Construct with a ``seed`` for reproducibility and a ``difficulty`` that
    scales both idiosyncratic volatility and event frequency. Call :meth:`tick`
    to advance one step (pure, testable), or :meth:`start` to let a background
    thread tick it on a wall-clock cadence and read it with :meth:`snapshot`.
    """

    #: multipliers applied to vol and event rate per difficulty label
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
        self.states: dict[str, CountryState] = {}
        for spec in UNIVERSE:
            cs = CountryState(spec=spec, yld=spec.base_yield,
                              prev_yld=spec.base_yield, open_yld=spec.base_yield)
            cs.refresh_price()
            cs.history.append(cs.price)
            self.states[spec.code] = cs
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

        A single shared ``risk`` draw correlates everyone this tick (a common
        factor), on top of each country's own noise and its decaying event
        pressure. This is what makes clusters move together without any explicit
        graph: the periphery all load positively on risk-off, the havens
        negatively.
        """
        vol_mult, ev_mult = self.DIFFICULTY.get(self.difficulty, (1.0, 1.0))
        with self._lock:
            # Common risk factor for the tick: small, mean-zero, but shared.
            common = self.rng.standard_normal() * 0.6 * vol_mult   # in bp
            for cs in self.states.values():
                s = cs.spec
                cs.prev_yld = cs.yld
                # Idiosyncratic jitter + the shared common factor via beta.
                idio = self.rng.standard_normal() * s.tick_vol_bp * vol_mult
                shock_bp = idio + s.beta_riskoff * common
                # Mean reversion toward base + current event pressure.
                target = s.base_yield + cs.pressure
                cs.yld += (target - cs.yld) * s.kappa + shock_bp / 100.0
                # Event pressure fades every tick (half-life ~ a few seconds).
                cs.pressure *= 0.97
                cs.refresh_price()
                cs.history.append(cs.price)
            # Maybe fire a geopolitical event this tick (Poisson thinning).
            base_rate_per_tick = 0.012 * ev_mult   # ~1 event / 12s at Normale
            if self.rng.random() < base_rate_per_tick:
                self._fire_event()
            self.ticks += 1

    def _fire_event(self) -> None:
        """Pick a template, resolve its target, apply the yield shock. Locked."""
        tpl = self.EVENT_RNG_CHOICE(self)
        vol_mult, _ = self.DIFFICULTY.get(self.difficulty, (1.0, 1.0))
        mag = tpl.risk * (0.7 + 0.6 * self.rng.random())   # ±30% size jitter

        if tpl.scope == "global":
            affected = {c: 1.0 for c in self.states}
            label = "Global"
        elif tpl.scope == "region":
            region = self.rng.choice(REGIONS)
            affected = {c: 1.0 for c, st in self.states.items()
                        if st.spec.region == region}
            label = region
        else:  # country — splashes onto regional peers through `spill`
            code = self.rng.choice(list(self.states))
            region = self.states[code].spec.region
            affected = {code: 1.0}
            for c, st in self.states.items():
                if c != code and st.spec.region == region:
                    affected[c] = tpl.spill
            label = self.states[code].spec.name

        for code, w in affected.items():
            cs = self.states[code]
            bp = cs.spec.beta_riskoff * mag * w
            cs.yld += (bp / 100.0) * 0.5        # half as an instant jump…
            cs.pressure += (bp / 100.0) * 0.5   # …half as fading pressure
            cs.refresh_price()

        self.events.appendleft(LiveEvent(
            ts=time.time(), category=tpl.category, scope=tpl.scope,
            text=tpl.headline.format(t=label),
            risk=mag,
        ))

    @staticmethod
    def EVENT_RNG_CHOICE(self: "RateWorld") -> EventTemplate:
        """Uniform pick from the catalogue (kept separate for easy override)."""
        return EVENT_CATALOGUE[int(self.rng.integers(len(EVENT_CATALOGUE)))]

    # ------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        """A consistent, lock-safe copy of the world for the UI to render."""
        with self._lock:
            de = self.states["DE"].yld
            rows = []
            for cs in self.states.values():
                s = cs.spec
                rows.append({
                    "code": s.code, "name": s.name, "flag": s.flag,
                    "region": s.region, "rating": s.rating,
                    "yield": cs.yld,
                    "price": cs.price,
                    "dv01": cs.dv01,
                    "chg_tick_bp": (cs.yld - cs.prev_yld) * 100.0,
                    "chg_session_bp": (cs.yld - cs.open_yld) * 100.0,
                    "spread_vs_bund_bp": (cs.yld - de) * 100.0,
                    "coupon": s.coupon, "freq": s.freq,
                })
            events = [{"ts": e.ts, "category": e.category, "scope": e.scope,
                       "text": e.text, "risk": e.risk} for e in self.events]
            return {
                "rows": rows, "events": events, "ticks": self.ticks,
                "elapsed": time.time() - self.started_at,
                "difficulty": self.difficulty,
            }

    def country(self, code: str) -> dict:
        """Live single-country view (mid, dv01, recent price history)."""
        with self._lock:
            cs = self.states[code]
            s = cs.spec
            return {
                "code": s.code, "name": s.name, "flag": s.flag,
                "region": s.region, "rating": s.rating, "coupon": s.coupon,
                "freq": s.freq, "yield": cs.yld, "price": cs.price,
                "dv01": cs.dv01, "history": list(cs.history),
                "spread_vs_bund_bp": (cs.yld - self.states["DE"].yld) * 100.0,
            }

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
    "price_from_yield", "price_dv01", "CountrySpec", "UNIVERSE", "TTM_YEARS",
    "EventTemplate", "EVENT_CATALOGUE", "REGIONS", "CountryState", "LiveEvent",
    "RateWorld",
]
