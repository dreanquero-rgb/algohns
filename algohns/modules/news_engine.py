"""Combinatorial news engine: a large, structured event space.

**The problem with a hand-written event list.** The first version of the world
simulation carried ~50 hand-written headlines. That is fine as a demo and wrong
as a forward test: with so few distinct events, every run recycles the same
handful of shocks, a portfolio can be tuned against them, and the whole thing
reads as a game rather than a risk model.

**The fix is structure, not more typing.** A news item is decomposed into
independent axes, and the event space is their product:

    frame  x  action  x  driver  x  magnitude  x  subject

* **frame** — the kind of thing happening (rate decision, guidance revision,
  sovereign stress, supply disruption, ...), which fixes the category, the
  scope and the shape of the economic impact;
* **action** — the specific move within that frame ("cuts", "raises",
  "suspends"), which fixes the sign;
* **driver** — the stated cause ("on weaker enterprise demand", "after a
  ratings review"), which is what makes two otherwise identical prints read as
  different news;
* **magnitude** — marginal / moderate / major / severe, a multiplier on every
  impact channel, which is what stops severity being a label with no teeth;
* **subject** — the company, sector, country, region or chokepoint hit.

That yields thousands of distinct *news kinds* before a subject is even
substituted, and six figures of distinct headlines with one. Crucially the
headline is not decoration: every component feeds the quantitative impact, so
the text and the shock always agree.

**What a news item does to the price.** It does not overwrite the path. Each
item carries four channels that re-parameterise the stochastic process
(``algohns.modules.stochastic``):

* ``equity_shock``     a discrete jump in the affected names (Merton jump);
* ``market_shock``     the same, on the global market factor;
* ``drift_change``     a persistent change in expected growth while active;
* ``vol_multiplier``   a regime change in volatility while active.

So the price keeps obeying its SDE and the news decides which regime it obeys
it in — which is the mechanism the forward test is built around.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

__all__ = [
    "MAGNITUDES",
    "NewsFrame",
    "NewsKind",
    "NEWS_FRAMES",
    "news_kinds",
    "catalogue_stats",
    "sample_news",
    "SampledNews",
]


# ---------------------------------------------------------------------------
# Magnitude bands.  One multiplier drives every impact channel, so "severe"
# is a quantitative statement rather than a label.
# ---------------------------------------------------------------------------
MAGNITUDES: dict[str, dict[str, float]] = {
    "marginal": {"scale": 0.40, "vol": 1.10, "rate": 3.0},
    "moderate": {"scale": 1.00, "vol": 1.35, "rate": 1.0},
    "major":    {"scale": 1.90, "vol": 1.90, "rate": 0.30},
    "severe":   {"scale": 3.20, "vol": 2.80, "rate": 0.07},
}


# Magnitude weights are normalised so decomposing a frame into bands
# redistributes its arrival rate instead of multiplying it: a frame's total
# intensity is exactly ``annual_rate``, however finely it is split. Without
# this, adding wording or a band would silently make the world more eventful.
_RATE_WEIGHT_TOTAL = sum(m["rate"] for m in MAGNITUDES.values())


@dataclass(frozen=True)
class NewsFrame:
    """A family of related events sharing a scope and an impact shape."""

    key: str
    category: str
    scope: str                     # company | sector | country | region | global | chokepoint
    actions: tuple[str, ...]
    drivers: tuple[str, ...]
    template: str = "{subject} {action} {driver}"
    favourable: bool = False
    annual_rate: float = 0.5       # base intensity before the magnitude split
    equity: tuple[float, float] = (0.0, 0.0)
    market: tuple[float, float] = (0.0, 0.0)
    impairment: tuple[float, float] = (0.0, 0.0)
    drift: tuple[float, float] = (0.0, 0.0)
    duration_days: tuple[int, int] = (20, 120)
    sector_filter: tuple[str, ...] = ()


@dataclass(frozen=True)
class NewsKind:
    """One fully-specified news kind: a frame at an action/driver/magnitude."""

    key: str
    frame_key: str
    category: str
    scope: str
    action: str
    driver: str
    magnitude: str
    template: str
    favourable: bool
    annual_rate: float
    equity: tuple[float, float]
    market: tuple[float, float]
    impairment: tuple[float, float]
    drift: tuple[float, float]
    vol_multiplier: float
    duration_days: tuple[int, int]
    sector_filter: tuple[str, ...]

    def headline(self, subject: str) -> str:
        text = self.template.format(subject=subject, action=self.action,
                                    driver=self.driver)
        return text[0].upper() + text[1:] if text else text


# ---------------------------------------------------------------------------
# Frames.  Wording is deliberately in the register of a terminal news feed.
# ---------------------------------------------------------------------------
_CORP_DRIVERS = (
    "on weaker enterprise demand", "after a sharp inventory build",
    "citing input-cost inflation", "following a ratings review",
    "as order backlogs shorten", "after an adverse currency move",
    "on softer consumer volumes", "following a supply qualification delay",
)
_CORP_UP_DRIVERS = (
    "on stronger enterprise demand", "after a record order intake",
    "citing margin expansion", "following a favourable ratings action",
    "as backlogs lengthen", "on a favourable currency move",
    "after a successful product ramp", "following a large design win",
)
_MACRO_DRIVERS = (
    "as core inflation surprises higher", "after a weak labour-market print",
    "citing financial-stability concerns", "following a fiscal slippage",
    "as growth momentum fades", "after an energy-price shock",
    "on deteriorating external balances",
)
_MACRO_UP_DRIVERS = (
    "as core inflation surprises lower", "after a strong labour-market print",
    "citing improved financial conditions", "following a credible fiscal plan",
    "as growth momentum builds", "after an energy-price retreat",
    "on improving external balances",
)
_SUPPLY_DRIVERS = (
    "after a fire at a key facility", "following a prolonged labour action",
    "on a critical equipment failure", "after a severe weather event",
    "citing a customs blockage", "following a cyber incident",
    "on a single-source component shortage",
)
_GEO_DRIVERS = (
    "as tensions escalate", "following a breakdown in talks",
    "after new export restrictions", "citing national-security grounds",
    "following a maritime incident", "as sanctions are widened",
    "after a contested election result",
)
_GEO_UP_DRIVERS = (
    "as tensions de-escalate", "following a breakthrough in talks",
    "after export restrictions are eased", "citing a security accord",
    "following a reopened corridor", "as sanctions are relaxed",
    "after an orderly transition",
)

NEWS_FRAMES: tuple[NewsFrame, ...] = (
    # ------------------------------------------------ company fundamentals
    NewsFrame("guidance_down", "company", "company",
              ("cuts full-year guidance", "trims revenue guidance",
               "lowers margin guidance", "withdraws its outlook"),
              _CORP_DRIVERS, annual_rate=1.8,
              equity=(-0.22, -0.05), drift=(-0.020, -0.004),
              duration_days=(25, 130)),
    NewsFrame("guidance_up", "company", "company",
              ("raises full-year guidance", "lifts revenue guidance",
               "upgrades margin guidance", "reinstates a stronger outlook"),
              _CORP_UP_DRIVERS, favourable=True, annual_rate=1.6,
              equity=(0.04, 0.18), drift=(0.004, 0.018),
              duration_days=(25, 130)),
    NewsFrame("earnings_miss", "company", "company",
              ("misses consensus earnings", "reports a revenue shortfall",
               "posts a margin compression", "books an impairment charge"),
              _CORP_DRIVERS, annual_rate=4.0,
              equity=(-0.18, -0.03), drift=(-0.012, -0.002),
              duration_days=(5, 40)),
    NewsFrame("earnings_beat", "company", "company",
              ("beats consensus earnings", "reports a revenue upside",
               "posts a margin expansion", "releases a provision"),
              _CORP_UP_DRIVERS, favourable=True, annual_rate=4.5,
              equity=(0.03, 0.16), drift=(0.002, 0.012),
              duration_days=(5, 40)),
    NewsFrame("dividend_cut", "company", "company",
              ("cuts its dividend", "suspends its dividend",
               "cancels its buyback", "defers its capital return"),
              _CORP_DRIVERS, annual_rate=0.5,
              equity=(-0.20, -0.05), drift=(-0.010, -0.002),
              duration_days=(30, 180)),
    NewsFrame("capital_return", "company", "company",
              ("raises its dividend", "announces a buyback",
               "accelerates its capital return", "declares a special dividend"),
              _CORP_UP_DRIVERS, favourable=True, annual_rate=1.1,
              equity=(0.02, 0.10), drift=(0.002, 0.008),
              duration_days=(30, 180)),
    NewsFrame("mna_target", "company", "company",
              ("receives a takeover approach", "agrees to be acquired",
               "confirms merger talks", "attracts an activist stake"),
              ("at a premium to the last close", "in an all-cash transaction",
               "in a stock-for-stock deal", "subject to regulatory clearance",
               "following a strategic review", "after a competing bid",
               "with management support"),
              favourable=True, annual_rate=0.8,
              equity=(0.08, 0.34), duration_days=(20, 160)),
    NewsFrame("governance", "company", "company",
              ("discloses accounting irregularities", "delays its filing",
               "replaces its chief executive", "reports a material weakness"),
              ("pending an internal review", "after an auditor resignation",
               "following a whistleblower report", "amid a regulatory inquiry",
               "after a restatement", "following a board dispute",
               "pending a forensic audit"),
              annual_rate=0.35,
              equity=(-0.45, -0.12), drift=(-0.030, -0.008),
              duration_days=(60, 400)),
    NewsFrame("litigation", "company", "company",
              ("faces a regulatory penalty", "loses a patent ruling",
               "settles a class action", "is placed under antitrust review"),
              ("with a record fine", "pending appeal", "on competition grounds",
               "after a multi-year probe", "with behavioural remedies",
               "following a court injunction", "under a consent decree"),
              annual_rate=0.9,
              equity=(-0.20, -0.03), drift=(-0.012, -0.002),
              duration_days=(30, 300)),
    NewsFrame("product", "company", "company",
              ("launches a category-defining product", "wins a multi-year contract",
               "secures a strategic partnership", "clears a key certification"),
              _CORP_UP_DRIVERS, favourable=True, annual_rate=1.3,
              equity=(0.05, 0.28), drift=(0.006, 0.030),
              duration_days=(60, 400)),

    # ------------------------------------------------------ supply / ops
    NewsFrame("plant_outage", "operations", "company",
              ("halts production at a key plant", "declares force majeure",
               "suspends shipments", "idles a production line"),
              _SUPPLY_DRIVERS, annual_rate=0.9,
              equity=(-0.14, -0.02), impairment=(0.15, 0.55),
              duration_days=(10, 120)),
    NewsFrame("cyber", "operations", "company",
              ("reports a cyber intrusion", "takes systems offline",
               "discloses a data breach", "restores operations after an attack"),
              ("with operations degraded", "affecting order processing",
               "after ransomware encryption", "with customer data exposed",
               "following a supplier compromise", "under regulatory notification",
               "with manual workarounds in place"),
              annual_rate=0.8,
              equity=(-0.14, -0.02), impairment=(0.10, 0.45),
              duration_days=(5, 60)),
    NewsFrame("chokepoint", "operations", "chokepoint",
              ("is closed to traffic", "faces severe congestion",
               "reopens under restrictions", "imposes transit limits"),
              _SUPPLY_DRIVERS, annual_rate=0.6,
              equity=(-0.10, -0.02), market=(-0.04, -0.005),
              impairment=(0.25, 0.70), duration_days=(15, 150)),
    NewsFrame("logistics", "operations", "country",
              ("sees port throughput collapse", "faces a rail strike",
               "suspends air-freight capacity", "imposes export licensing"),
              _SUPPLY_DRIVERS, annual_rate=0.7,
              equity=(-0.07, -0.01), impairment=(0.10, 0.40),
              duration_days=(7, 90)),
    NewsFrame("grid", "operations", "country",
              ("suffers a grid failure", "imposes power rationing",
               "restores supply after an outage", "curtails industrial load"),
              ("after a generation shortfall", "during a demand peak",
               "following a transmission fault", "amid a fuel shortage",
               "after a severe weather event", "under an emergency protocol",
               "with rolling blackouts"),
              annual_rate=0.5,
              equity=(-0.06, -0.01), impairment=(0.12, 0.45),
              duration_days=(3, 45)),

    # ------------------------------------------------------- monetary
    NewsFrame("policy_tighten", "monetary", "global",
              ("delivers a surprise rate increase", "signals further tightening",
               "accelerates balance-sheet runoff", "raises its terminal-rate guidance"),
              _MACRO_DRIVERS, annual_rate=1.0,
              market=(-0.11, -0.02), drift=(-0.018, -0.004),
              duration_days=(40, 300)),
    NewsFrame("policy_ease", "monetary", "global",
              ("cuts rates by more than expected", "signals an easing cycle",
               "ends balance-sheet runoff", "lowers its terminal-rate guidance"),
              _MACRO_UP_DRIVERS, favourable=True, annual_rate=0.9,
              market=(0.02, 0.10), drift=(0.004, 0.018),
              duration_days=(40, 300)),
    NewsFrame("inflation", "monetary", "global",
              ("prints hotter-than-expected inflation", "reports broadening price pressure",
               "posts an upside surprise in core prices", "revises inflation higher"),
              _MACRO_DRIVERS, annual_rate=0.9,
              market=(-0.08, -0.01), drift=(-0.010, -0.002),
              duration_days=(60, 300)),
    NewsFrame("disinflation", "monetary", "global",
              ("prints cooler-than-expected inflation", "reports broad-based disinflation",
               "posts a downside surprise in core prices", "revises inflation lower"),
              _MACRO_UP_DRIVERS, favourable=True, annual_rate=0.9,
              market=(0.01, 0.07), drift=(0.002, 0.010),
              duration_days=(60, 300)),
    NewsFrame("recession", "monetary", "global",
              ("enters a technical recession", "posts a second quarterly contraction",
               "reports a sharp activity downturn", "sees leading indicators roll over"),
              _MACRO_DRIVERS, annual_rate=0.30,
              market=(-0.22, -0.06), impairment=(0.05, 0.20),
              drift=(-0.025, -0.006), duration_days=(200, 700)),
    NewsFrame("expansion", "monetary", "global",
              ("posts an upside growth surprise", "sees activity reaccelerate",
               "reports a broad-based expansion", "sees leading indicators turn up"),
              _MACRO_UP_DRIVERS, favourable=True, annual_rate=0.7,
              market=(0.03, 0.13), drift=(0.006, 0.022),
              duration_days=(150, 540)),
    NewsFrame("credit_stress", "monetary", "sector",
              ("faces a funding squeeze", "sees spreads gap wider",
               "reports deposit outflows", "tightens lending standards sharply"),
              _MACRO_DRIVERS, annual_rate=0.45,
              equity=(-0.28, -0.07), market=(-0.09, -0.02),
              duration_days=(60, 360), sector_filter=("Financials",)),
    NewsFrame("sovereign", "monetary", "country",
              ("sees its spread widen sharply", "is placed on negative watch",
               "faces a failed auction", "is downgraded by an agency"),
              _MACRO_DRIVERS, annual_rate=0.40,
              equity=(-0.24, -0.06), market=(-0.06, -0.01),
              duration_days=(90, 480)),
    NewsFrame("sovereign_good", "monetary", "country",
              ("sees its spread compress", "is placed on positive watch",
               "prices a heavily oversubscribed auction", "is upgraded by an agency"),
              _MACRO_UP_DRIVERS, favourable=True, annual_rate=0.35,
              equity=(0.02, 0.11), market=(0.005, 0.03),
              duration_days=(90, 480)),
    NewsFrame("currency", "monetary", "country",
              ("sees its currency slide", "imposes capital controls",
               "abandons its peg", "intervenes to defend the exchange rate"),
              _MACRO_DRIVERS, annual_rate=0.5,
              equity=(-0.22, -0.05), duration_days=(45, 330)),

    # ----------------------------------------------------- geopolitical
    NewsFrame("conflict", "geopolitical", "region",
              ("sees hostilities escalate", "faces a wider mobilisation",
               "reports cross-border strikes", "enters an open conflict"),
              _GEO_DRIVERS, annual_rate=0.30,
              equity=(-0.26, -0.08), market=(-0.15, -0.05),
              impairment=(0.20, 0.60), drift=(-0.025, -0.008),
              duration_days=(150, 700)),
    NewsFrame("detente", "geopolitical", "region",
              ("reaches a ceasefire", "signs a security accord",
               "reopens trade corridors", "concludes a peace framework"),
              _GEO_UP_DRIVERS, favourable=True, annual_rate=0.30,
              equity=(0.03, 0.13), market=(0.01, 0.05),
              drift=(0.004, 0.018), duration_days=(60, 300)),
    NewsFrame("tariff", "geopolitical", "country",
              ("faces new import tariffs", "is hit by retaliatory duties",
               "sees quotas imposed", "enters a trade dispute"),
              _GEO_DRIVERS, annual_rate=0.7,
              equity=(-0.12, -0.02), market=(-0.04, -0.005),
              drift=(-0.014, -0.003), duration_days=(150, 700)),
    NewsFrame("trade_deal", "geopolitical", "region",
              ("concludes a free-trade agreement", "removes tariff barriers",
               "harmonises technical standards", "opens procurement access"),
              _GEO_UP_DRIVERS, favourable=True, annual_rate=0.55,
              equity=(0.02, 0.09), drift=(0.004, 0.015),
              duration_days=(150, 700)),
    NewsFrame("export_controls", "geopolitical", "sector",
              ("is placed under export licensing", "faces technology restrictions",
               "is added to an entity list", "sees equipment shipments blocked"),
              _GEO_DRIVERS, annual_rate=0.6,
              equity=(-0.16, -0.04), impairment=(0.10, 0.35),
              drift=(-0.020, -0.005), duration_days=(300, 1000),
              sector_filter=("Semis", "Semis Equipment", "Hardware")),
    NewsFrame("sanctions", "geopolitical", "country",
              ("is placed under sanctions", "faces secondary sanctions",
               "is cut off from payment systems", "sees asset freezes imposed"),
              _GEO_DRIVERS, annual_rate=0.5,
              equity=(-0.18, -0.04), impairment=(0.08, 0.30),
              duration_days=(300, 1000)),
    NewsFrame("politics", "geopolitical", "country",
              ("faces a government collapse", "sees an unexpected election result",
               "enters a constitutional crisis", "announces an emergency budget"),
              _GEO_DRIVERS, annual_rate=0.6,
              equity=(-0.14, 0.03), market=(-0.03, 0.01),
              duration_days=(20, 200)),

    # --------------------------------------------------------- natural
    NewsFrame("quake", "natural", "country",
              ("is struck by a major earthquake", "reports widespread structural damage",
               "declares a state of emergency", "begins reconstruction"),
              ("with industrial capacity offline", "after a high-magnitude event",
               "with transport links severed", "following aftershocks",
               "under an emergency decree", "with ports inoperable",
               "amid a humanitarian response"),
              annual_rate=0.5,
              equity=(-0.13, -0.03), impairment=(0.18, 0.60),
              duration_days=(40, 260)),
    NewsFrame("flood", "natural", "country",
              ("is hit by severe flooding", "evacuates industrial districts",
               "reports crop and plant damage", "faces a prolonged cleanup"),
              ("after record rainfall", "following a levee failure",
               "with logistics hubs submerged", "amid a storm surge",
               "under a disaster declaration", "with power substations flooded",
               "after an upstream dam release"),
              annual_rate=0.8,
              equity=(-0.08, -0.01), impairment=(0.10, 0.38),
              duration_days=(25, 160)),
    NewsFrame("storm", "natural", "country",
              ("is struck by a major storm", "closes refineries and ports",
               "suspends offshore production", "restarts after a hurricane"),
              ("with a landfall at peak intensity", "after mandatory evacuations",
               "with grid damage widespread", "amid a fuel-supply disruption",
               "under a federal emergency", "with shipping rerouted",
               "following an extended outage"),
              annual_rate=0.9,
              equity=(-0.07, -0.01), impairment=(0.08, 0.32),
              duration_days=(10, 90)),
    NewsFrame("drought", "natural", "country",
              ("faces a severe drought", "imposes water rationing",
               "curtails hydro generation", "restricts agricultural withdrawal"),
              ("after a record-dry season", "with reservoirs at historic lows",
               "amid a heatwave", "under an emergency water order",
               "with river transport restricted", "following crop failure",
               "as cooling water runs short"),
              annual_rate=0.6,
              equity=(-0.06, -0.01), impairment=(0.08, 0.28),
              duration_days=(120, 420)),
    NewsFrame("pandemic", "health", "global",
              ("declares a public-health emergency", "imposes mobility restrictions",
               "reports sustained community transmission", "lifts emergency measures"),
              ("with manufacturing curtailed", "after a novel pathogen emerges",
               "under a containment protocol", "with borders restricted",
               "amid hospital-capacity strain", "following a variant escape",
               "with schools and offices closed"),
              annual_rate=0.10,
              equity=(-0.20, -0.06), market=(-0.30, -0.12),
              impairment=(0.25, 0.70), drift=(-0.020, 0.002),
              duration_days=(150, 540)),

    # ------------------------------------------------------- technology
    NewsFrame("tech_leap", "technology", "sector",
              ("sees a step-change in capability", "reports a major efficiency gain",
               "announces a new architecture", "clears a scaling milestone"),
              _CORP_UP_DRIVERS, favourable=True, annual_rate=0.8,
              equity=(0.08, 0.38), drift=(0.015, 0.055),
              duration_days=(150, 700),
              sector_filter=("Semis", "Software", "Comm. Services")),
    NewsFrame("disruption", "technology", "sector",
              ("faces a disruptive new entrant", "sees pricing power erode",
               "loses share to a substitute", "confronts open-source displacement"),
              _CORP_DRIVERS, annual_rate=0.7,
              equity=(-0.22, -0.05), drift=(-0.028, -0.007),
              duration_days=(150, 800)),
    NewsFrame("capex_cycle", "sector", "sector",
              ("enters a record investment cycle", "reports an order-book inflection",
               "sees capacity additions accelerate", "raises its capex outlook"),
              _CORP_UP_DRIVERS, favourable=True, annual_rate=0.8,
              equity=(0.07, 0.32), drift=(0.012, 0.045),
              duration_days=(150, 700)),
    NewsFrame("inventory_cycle", "sector", "sector",
              ("enters an inventory correction", "sees double-ordering unwind",
               "reports channel destocking", "cuts utilisation rates"),
              _CORP_DRIVERS, annual_rate=0.8,
              equity=(-0.20, -0.04), impairment=(0.05, 0.20),
              drift=(-0.018, -0.004), duration_days=(120, 500)),
    NewsFrame("commodity", "sector", "sector",
              ("sees input costs spike", "faces a raw-material squeeze",
               "reports a shortage of a critical input", "benefits from falling input costs"),
              ("after a producer curtailment", "on export restrictions",
               "following a mine outage", "amid speculative positioning",
               "after an inventory drawdown", "on freight-rate inflation",
               "following a substitution failure"),
              annual_rate=0.9,
              equity=(-0.12, 0.14), impairment=(0.04, 0.20),
              duration_days=(90, 400)),
    NewsFrame("valuation", "sector", "sector",
              ("sees a sharp multiple compression", "unwinds a crowded position",
               "faces a positioning flush", "re-rates lower on higher discount rates"),
              _MACRO_DRIVERS, annual_rate=0.45,
              equity=(-0.38, -0.14), market=(-0.08, -0.02),
              drift=(-0.018, -0.004), duration_days=(100, 500)),
)


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------
def _scaled(rng_range: tuple[float, float], scale: float) -> tuple[float, float]:
    lo, hi = rng_range
    a, b = lo * scale, hi * scale
    return (min(a, b), max(a, b))


@lru_cache(maxsize=1)
def news_kinds() -> tuple[NewsKind, ...]:
    """The full expanded space of news kinds (frames x action x driver x magnitude).

    Cached because it is deterministic and pure: the same catalogue every run,
    which is what makes a seeded world reproducible.
    """
    out: list[NewsKind] = []
    for f in NEWS_FRAMES:
        for ai, action in enumerate(f.actions):
            for di, driver in enumerate(f.drivers):
                for mag, spec in MAGNITUDES.items():
                    scale = spec["scale"]
                    out.append(NewsKind(
                        key=f"{f.key}.{ai}.{di}.{mag}",
                        frame_key=f.key, category=f.category, scope=f.scope,
                        action=action, driver=driver, magnitude=mag,
                        template=f.template, favourable=f.favourable,
                        # Splitting one frame rate across its variants keeps the
                        # frame's total arrival rate invariant to how finely it
                        # is decomposed - otherwise adding wording would
                        # silently make the world more eventful.
                        annual_rate=f.annual_rate
                        * (spec["rate"] / _RATE_WEIGHT_TOTAL)
                        / (len(f.actions) * len(f.drivers)),
                        equity=_scaled(f.equity, scale),
                        market=_scaled(f.market, scale),
                        impairment=_scaled(f.impairment, min(scale, 1.4)),
                        drift=_scaled(f.drift, scale),
                        vol_multiplier=spec["vol"],
                        duration_days=f.duration_days,
                        sector_filter=f.sector_filter,
                    ))
    return tuple(out)


def catalogue_stats(subject_counts: dict[str, int] | None = None) -> dict[str, int | float]:
    """Size of the event space, for the UI to state plainly.

    ``subject_counts`` maps a scope to how many subjects exist for it, so the
    distinct-headline count reflects the world actually loaded.
    """
    kinds = news_kinds()
    counts = subject_counts or {}
    headlines = 0
    for k in kinds:
        headlines += max(counts.get(k.scope, 1), 1)
    favourable = sum(1 for k in kinds if k.favourable)
    return {
        "frames": len(NEWS_FRAMES),
        "kinds": len(kinds),
        "distinct_headlines": headlines,
        "favourable_kinds": favourable,
        "favourable_share": round(favourable / len(kinds), 3) if kinds else 0.0,
        "categories": len({k.category for k in kinds}),
        "total_annual_rate": round(sum(k.annual_rate for k in kinds), 2),
    }


@dataclass
class SampledNews:
    """A dated, quantified news item drawn from the catalogue."""

    day: int
    kind: NewsKind
    subject: str
    headline: str
    equity_shock: float
    market_shock: float
    impairment: float
    drift_change: float
    vol_multiplier: float
    duration_days: int

    @property
    def end_day(self) -> int:
        return self.day + self.duration_days

    def active_on(self, day: int) -> bool:
        return self.day <= day < self.end_day


def _u(rng: np.random.Generator, bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    return lo if lo == hi else float(rng.uniform(lo, hi))


def sample_news(
    horizon_days: int,
    subjects: dict[str, list[str]],
    *,
    seed: int = 42,
    intensity: float = 1.0,
) -> list[SampledNews]:
    """Draw a dated news timeline over `horizon_days`.

    Each news kind is an independent Poisson process, so the world's event flow
    is the superposition of thousands of thin processes rather than a scripted
    sequence. All draws come from one seeded generator in a fixed order, so the
    seed determines the world completely.
    """
    if horizon_days < 1:
        raise ValueError("horizon_days must be positive")
    if intensity <= 0:
        raise ValueError("intensity must be positive")

    rng = np.random.default_rng(seed)
    years = horizon_days / 365.0
    out: list[SampledNews] = []

    for kind in news_kinds():                     # deterministic order
        pool = subjects.get(kind.scope) or []
        if kind.scope == "global":
            pool = ["the global economy"]
        if not pool:
            continue
        expected = kind.annual_rate * years * intensity
        if expected <= 0:
            continue
        for _ in range(int(rng.poisson(expected))):
            subject = str(rng.choice(sorted(pool)))
            out.append(SampledNews(
                day=int(rng.integers(0, horizon_days)),
                kind=kind,
                subject=subject,
                headline=kind.headline(subject),
                equity_shock=_u(rng, kind.equity),
                market_shock=_u(rng, kind.market),
                impairment=_u(rng, kind.impairment),
                drift_change=_u(rng, kind.drift),
                vol_multiplier=kind.vol_multiplier,
                duration_days=int(rng.integers(kind.duration_days[0],
                                               kind.duration_days[1] + 1)),
            ))

    out.sort(key=lambda n: (n.day, n.kind.key))
    return out


def expected_market_drag(intensity: float = 1.0) -> float:
    """Annualised expected contribution of the catalogue to the market factor.

    The catalogue is not drift-neutral, so the simulator subtracts this to keep
    a declared market drift meaning the *unconditional* expectation.
    """
    return intensity * sum(
        k.annual_rate * (k.market[0] + k.market[1]) / 2.0 for k in news_kinds()
    )
