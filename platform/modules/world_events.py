"""Weighted random world-event generator for the forward simulation.

Events arrive as independent Poisson processes, one per template, each with
its own annual intensity. That gives a world where a pandemic is rare and an
earnings miss is common, without hand-scripting a timeline.

**Three design points worth defending:**

1. **Positive events are not decoration.** A generator with only disasters
   produces a world where every portfolio loses, which is useless for
   forward testing — you learn nothing from a rigged downside. Roughly a
   third of the catalogue is favourable (rate cuts, breakthroughs, trade
   deals, earnings beats), so the unconditional drift stays realistic and
   the interesting question becomes *which* portfolio survives *which*
   path.

2. **Intensities are calibrated to rough historical base rates**, stated in
   each template. A major pandemic at 0.025/yr is "once in forty years"; a
   recession at 0.13/yr is "once in eight". These are order-of-magnitude
   judgements, not fitted parameters, and they are the first thing to
   revise when the historical validation harness exists.

3. **Everything is seeded.** One RNG, drawn in a fixed order, so a seed
   fully determines a world. Two portfolios can then be compared on the
   *identical* path, which is the only comparison that means anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

__all__ = [
    "expected_event_market_drift",
    "EventCategory",
    "EventScope",
    "EventTemplate",
    "WorldEvent",
    "EVENT_CATALOGUE",
    "sample_events",
]

TRADING_DAYS_PER_YEAR = 252


class EventCategory(str, Enum):
    GEOPOLITICAL = "geopolitical"
    HEALTH = "health"
    NATURAL = "natural"
    MONETARY = "monetary"
    SECTOR = "sector"
    COMPANY = "company"
    INFRASTRUCTURE = "infrastructure"
    TECHNOLOGY = "technology"


class EventScope(str, Enum):
    """Who the event hits, which decides how seeds are drawn."""

    GLOBAL = "global"          # every node, via the market factor
    REGION = "region"          # all companies with revenue in a region
    COUNTRY = "country"        # all companies with revenue in a country
    SECTOR = "sector"          # all companies in a sector
    COMPANY = "company"        # one company
    CHOKEPOINT = "chokepoint"  # a named set of logistics/foundry nodes


@dataclass(frozen=True)
class EventTemplate:
    """A kind of thing that can happen, plus how often and how hard."""

    key: str
    category: EventCategory
    scope: EventScope
    headline: str                       # supports {target} and {pct}
    # Expected occurrences per year, across the whole world.
    annual_rate: float
    # Operational impairment inflicted on affected nodes, as (low, high).
    impairment: tuple[float, float] = (0.0, 0.0)
    # Direct equity shock on affected names, as (low, high). Negative = loss.
    equity_shock: tuple[float, float] = (0.0, 0.0)
    # Shock to the global market factor, as (low, high).
    market_shock: tuple[float, float] = (0.0, 0.0)
    duration_days: tuple[int, int] = (30, 90)
    # Permanent change to the affected companies' growth drift, annualised.
    drift_change: tuple[float, float] = (0.0, 0.0)
    # Multiplier on bankruptcy hazard while active.
    distress_multiplier: float = 1.0
    favourable: bool = False
    severity_label: str = "medio"
    # Restrict to these sectors / countries when the scope needs it.
    sector_filter: tuple[str, ...] = ()
    country_filter: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.annual_rate <= 0:
            raise ValueError(f"{self.key}: annual_rate deve essere positivo")
        for name, rng in (
            ("impairment", self.impairment),
            ("equity_shock", self.equity_shock),
            ("market_shock", self.market_shock),
            ("drift_change", self.drift_change),
        ):
            if rng[0] > rng[1]:
                raise ValueError(f"{self.key}: {name} invertito {rng}")
        if self.duration_days[0] > self.duration_days[1] or self.duration_days[0] < 1:
            raise ValueError(f"{self.key}: duration_days non valido")


@dataclass
class WorldEvent:
    """A sampled, dated instance of a template."""

    day: int
    template: EventTemplate
    target: str                      # country code, sector, ticker, or "world"
    targets_resolved: list[str] = field(default_factory=list)
    impairment: float = 0.0
    equity_shock: float = 0.0
    market_shock: float = 0.0
    duration_days: int = 30
    drift_change: float = 0.0
    headline: str = ""

    @property
    def category(self) -> EventCategory:
        return self.template.category

    @property
    def favourable(self) -> bool:
        return self.template.favourable

    @property
    def end_day(self) -> int:
        return self.day + self.duration_days

    def active_on(self, day: int) -> bool:
        return self.day <= day < self.end_day

    def to_dict(self) -> dict:
        return {
            "day": self.day,
            "key": self.template.key,
            "category": self.category.value,
            "scope": self.template.scope.value,
            "headline": self.headline,
            "target": self.target,
            "affected": self.targets_resolved,
            "impairment": round(self.impairment, 4),
            "equityShock": round(self.equity_shock, 4),
            "marketShock": round(self.market_shock, 4),
            "driftChange": round(self.drift_change, 5),
            "durationDays": self.duration_days,
            "favourable": self.favourable,
            "severity": self.template.severity_label,
        }


def _t(**kw) -> EventTemplate:
    return EventTemplate(**kw)


# Rates are annual intensities. The comment on each line is the base rate the
# number is meant to encode; that is the thing to argue with.
EVENT_CATALOGUE: list[EventTemplate] = [
    # ------------------------------------------------ GEOPOLITICAL --------
    _t(key="major_war", category=EventCategory.GEOPOLITICAL,
       scope=EventScope.REGION,
       headline="Conflitto armato su larga scala: {target} — mercati in fuga verso la qualità",
       annual_rate=0.08,  # ~1 ogni 12 anni un conflitto che muove i mercati globali
       impairment=(0.25, 0.65), equity_shock=(-0.30, -0.12),
       market_shock=(-0.18, -0.07), duration_days=(180, 720),
       drift_change=(-0.03, -0.01), distress_multiplier=2.5,
       severity_label="estremo"),
    _t(key="regional_conflict", category=EventCategory.GEOPOLITICAL,
       scope=EventScope.COUNTRY,
       headline="Escalation militare in {target}: interrotte le catene locali",
       annual_rate=0.28,
       impairment=(0.10, 0.35), equity_shock=(-0.15, -0.04),
       market_shock=(-0.05, -0.01), duration_days=(90, 365),
       distress_multiplier=1.6, severity_label="alto"),
    _t(key="trade_war", category=EventCategory.GEOPOLITICAL,
       scope=EventScope.COUNTRY,
       headline="Nuovi dazi su {target}: {pct} sulle importazioni",
       annual_rate=0.22,
       impairment=(0.05, 0.20), equity_shock=(-0.12, -0.03),
       market_shock=(-0.04, -0.01), duration_days=(180, 900),
       drift_change=(-0.015, -0.003), severity_label="medio"),
    _t(key="export_controls", category=EventCategory.GEOPOLITICAL,
       scope=EventScope.SECTOR,
       headline="Controlli all'export su {target}: tecnologia avanzata sotto licenza",
       annual_rate=0.20,
       impairment=(0.10, 0.30), equity_shock=(-0.14, -0.04),
       duration_days=(365, 1095), drift_change=(-0.02, -0.005),
       sector_filter=("Semis", "Semis Equipment"), severity_label="alto"),
    _t(key="sanctions", category=EventCategory.GEOPOLITICAL,
       scope=EventScope.COUNTRY,
       headline="Pacchetto di sanzioni verso {target}",
       annual_rate=0.20,
       impairment=(0.08, 0.28), equity_shock=(-0.13, -0.03),
       duration_days=(365, 1095), severity_label="medio"),
    _t(key="election_shock", category=EventCategory.GEOPOLITICAL,
       scope=EventScope.COUNTRY,
       headline="Esito elettorale inatteso in {target}: premio al rischio in salita",
       annual_rate=0.35,
       equity_shock=(-0.09, 0.05), market_shock=(-0.03, 0.01),
       duration_days=(20, 90), severity_label="basso"),
    _t(key="political_instability", category=EventCategory.GEOPOLITICAL,
       scope=EventScope.COUNTRY,
       headline="Crisi istituzionale in {target}: sospesi gli investimenti esteri",
       annual_rate=0.14,
       impairment=(0.05, 0.22), equity_shock=(-0.18, -0.06),
       duration_days=(90, 540), distress_multiplier=1.8,
       severity_label="alto"),
    _t(key="peace_deal", category=EventCategory.GEOPOLITICAL,
       scope=EventScope.REGION,
       headline="Accordo di pace in {target}: riaprono i corridoi commerciali",
       annual_rate=0.12, favourable=True,
       equity_shock=(0.03, 0.12), market_shock=(0.01, 0.05),
       duration_days=(60, 240), drift_change=(0.005, 0.02),
       severity_label="favorevole"),
    _t(key="trade_agreement", category=EventCategory.GEOPOLITICAL,
       scope=EventScope.REGION,
       headline="Nuovo accordo di libero scambio: {target}",
       annual_rate=0.18, favourable=True,
       equity_shock=(0.02, 0.09), duration_days=(180, 720),
       drift_change=(0.004, 0.015), severity_label="favorevole"),

    # ------------------------------------------------------- HEALTH -------
    _t(key="pandemic", category=EventCategory.HEALTH, scope=EventScope.GLOBAL,
       headline="Emergenza sanitaria globale: lockdown e arresto manifatturiero",
       annual_rate=0.025,  # ~1 ogni 40 anni
       impairment=(0.30, 0.70), equity_shock=(-0.22, -0.08),
       market_shock=(-0.34, -0.16), duration_days=(180, 540),
       drift_change=(-0.02, 0.005), distress_multiplier=3.0,
       severity_label="estremo"),
    _t(key="epidemic_regional", category=EventCategory.HEALTH,
       scope=EventScope.REGION,
       headline="Epidemia in {target}: restrizioni alla mobilità",
       annual_rate=0.12,
       impairment=(0.10, 0.30), equity_shock=(-0.10, -0.02),
       market_shock=(-0.05, -0.01), duration_days=(60, 240),
       distress_multiplier=1.5, severity_label="alto"),
    _t(key="medical_breakthrough", category=EventCategory.HEALTH,
       scope=EventScope.SECTOR,
       headline="Svolta terapeutica in {target}: approvazione accelerata",
       annual_rate=0.14, favourable=True,
       equity_shock=(0.06, 0.25), duration_days=(90, 365),
       drift_change=(0.01, 0.04), sector_filter=("Health Care",),
       severity_label="favorevole"),

    # ------------------------------------------------------ NATURAL ------
    _t(key="major_earthquake", category=EventCategory.NATURAL,
       scope=EventScope.COUNTRY,
       headline="Terremoto di forte magnitudo in {target}: impianti fermi",
       annual_rate=0.22,
       impairment=(0.20, 0.60), equity_shock=(-0.14, -0.04),
       duration_days=(45, 240), distress_multiplier=1.4,
       severity_label="alto"),
    _t(key="flood", category=EventCategory.NATURAL, scope=EventScope.COUNTRY,
       headline="Alluvione in {target}: distretti industriali allagati",
       annual_rate=0.34,
       impairment=(0.10, 0.35), equity_shock=(-0.08, -0.02),
       duration_days=(30, 150), severity_label="medio"),
    _t(key="hurricane", category=EventCategory.NATURAL,
       scope=EventScope.COUNTRY,
       headline="Uragano su {target}: raffinerie e porti chiusi",
       annual_rate=0.38,
       impairment=(0.10, 0.30), equity_shock=(-0.07, -0.01),
       duration_days=(15, 75), severity_label="medio"),
    _t(key="drought", category=EventCategory.NATURAL, scope=EventScope.COUNTRY,
       headline="Siccità severa in {target}: razionamento di acqua ed energia",
       annual_rate=0.24,
       impairment=(0.08, 0.25), equity_shock=(-0.06, -0.01),
       duration_days=(120, 400), severity_label="medio"),
    _t(key="volcanic", category=EventCategory.NATURAL, scope=EventScope.REGION,
       headline="Eruzione vulcanica: spazio aereo chiuso su {target}",
       annual_rate=0.07,
       impairment=(0.10, 0.28), equity_shock=(-0.06, -0.01),
       duration_days=(10, 60), severity_label="medio"),

    # ----------------------------------------------------- MONETARY ------
    _t(key="rate_shock_up", category=EventCategory.MONETARY,
       scope=EventScope.GLOBAL,
       headline="Stretta monetaria a sorpresa: {pct} sui tassi di riferimento",
       annual_rate=0.28,
       market_shock=(-0.12, -0.03), duration_days=(60, 300),
       drift_change=(-0.02, -0.005), distress_multiplier=1.5,
       severity_label="alto"),
    _t(key="rate_shock_down", category=EventCategory.MONETARY,
       scope=EventScope.GLOBAL,
       headline="Taglio dei tassi oltre le attese: {pct}",
       annual_rate=0.24, favourable=True,
       market_shock=(0.03, 0.10), duration_days=(60, 300),
       drift_change=(0.005, 0.02), severity_label="favorevole"),
    _t(key="inflation_spike", category=EventCategory.MONETARY,
       scope=EventScope.GLOBAL,
       headline="Inflazione oltre le attese: {pct} su base annua",
       annual_rate=0.22,
       market_shock=(-0.09, -0.02), duration_days=(90, 400),
       drift_change=(-0.012, -0.002), severity_label="medio"),
    _t(key="currency_crisis", category=EventCategory.MONETARY,
       scope=EventScope.COUNTRY,
       headline="Crisi valutaria in {target}: svalutazione del {pct}",
       annual_rate=0.16,
       equity_shock=(-0.25, -0.08), duration_days=(60, 365),
       distress_multiplier=2.0, severity_label="alto"),
    _t(key="sovereign_crisis", category=EventCategory.MONETARY,
       scope=EventScope.COUNTRY,
       headline="Crisi del debito sovrano: spread di {target} fuori controllo",
       annual_rate=0.10,
       equity_shock=(-0.28, -0.10), market_shock=(-0.07, -0.02),
       duration_days=(120, 540), distress_multiplier=2.2,
       severity_label="estremo"),
    _t(key="recession", category=EventCategory.MONETARY,
       scope=EventScope.GLOBAL,
       headline="Recessione confermata: contrazione del PIL per due trimestri",
       annual_rate=0.13,  # ~1 ogni 8 anni
       impairment=(0.05, 0.18), equity_shock=(-0.10, -0.03),
       market_shock=(-0.24, -0.10), duration_days=(270, 720),
       drift_change=(-0.025, -0.008), distress_multiplier=2.4,
       severity_label="estremo"),
    _t(key="credit_crunch", category=EventCategory.MONETARY,
       scope=EventScope.SECTOR,
       headline="Stretta creditizia: {target} sotto pressione di liquidità",
       annual_rate=0.11,
       equity_shock=(-0.30, -0.10), market_shock=(-0.10, -0.03),
       duration_days=(90, 400), distress_multiplier=2.6,
       sector_filter=("Financials",), severity_label="estremo"),
    _t(key="expansion", category=EventCategory.MONETARY,
       scope=EventScope.GLOBAL,
       headline="Crescita sopra le attese: revisione al rialzo delle stime",
       annual_rate=0.22, favourable=True,
       market_shock=(0.04, 0.14), duration_days=(180, 540),
       drift_change=(0.008, 0.025), severity_label="favorevole"),

    # ------------------------------------------------------- SECTOR ------
    _t(key="oil_shock", category=EventCategory.SECTOR,
       scope=EventScope.SECTOR,
       headline="Shock petrolifero: greggio {pct} in poche sedute",
       annual_rate=0.20,
       equity_shock=(0.08, 0.30), market_shock=(-0.08, -0.02),
       duration_days=(60, 300), sector_filter=("Energy",),
       severity_label="alto"),
    _t(key="commodity_squeeze", category=EventCategory.SECTOR,
       scope=EventScope.SECTOR,
       headline="Stretta sulle materie prime: {target} ai massimi storici",
       annual_rate=0.24,
       impairment=(0.05, 0.22), equity_shock=(-0.10, 0.15),
       duration_days=(90, 365), sector_filter=("Materials",),
       severity_label="medio"),
    _t(key="chip_shortage", category=EventCategory.SECTOR,
       scope=EventScope.SECTOR,
       headline="Carenza globale di semiconduttori: allocazione razionata",
       annual_rate=0.14,
       impairment=(0.15, 0.40), equity_shock=(-0.12, 0.10),
       duration_days=(180, 540), sector_filter=("Semis", "Cons. Discretionary"),
       severity_label="alto"),
    _t(key="bubble_burst", category=EventCategory.SECTOR,
       scope=EventScope.SECTOR,
       headline="Sgonfiamento delle valutazioni in {target}",
       annual_rate=0.13,
       equity_shock=(-0.42, -0.18), market_shock=(-0.09, -0.02),
       duration_days=(120, 540), drift_change=(-0.02, -0.005),
       distress_multiplier=1.7, severity_label="estremo"),
    _t(key="sector_boom", category=EventCategory.SECTOR,
       scope=EventScope.SECTOR,
       headline="Ciclo di investimenti record in {target}",
       annual_rate=0.28, favourable=True,
       equity_shock=(0.10, 0.38), duration_days=(180, 720),
       drift_change=(0.015, 0.05), severity_label="favorevole"),

    # ------------------------------------------------ INFRASTRUCTURE -----
    _t(key="chokepoint_closure", category=EventCategory.INFRASTRUCTURE,
       scope=EventScope.CHOKEPOINT,
       headline="Chiusura di un nodo logistico critico: {target}",
       annual_rate=0.18,
       impairment=(0.30, 0.75), equity_shock=(-0.12, -0.03),
       market_shock=(-0.04, -0.01), duration_days=(20, 150),
       severity_label="alto"),
    _t(key="cyberattack", category=EventCategory.INFRASTRUCTURE,
       scope=EventScope.COMPANY,
       headline="Attacco informatico su {target}: operatività compromessa",
       annual_rate=0.40,
       impairment=(0.15, 0.50), equity_shock=(-0.14, -0.03),
       duration_days=(7, 60), severity_label="medio"),
    _t(key="grid_failure", category=EventCategory.INFRASTRUCTURE,
       scope=EventScope.COUNTRY,
       headline="Blackout esteso in {target}: produzione sospesa",
       annual_rate=0.18,
       impairment=(0.15, 0.45), equity_shock=(-0.07, -0.01),
       duration_days=(3, 40), severity_label="medio"),
    _t(key="port_strike", category=EventCategory.INFRASTRUCTURE,
       scope=EventScope.COUNTRY,
       headline="Sciopero portuale in {target}: container fermi",
       annual_rate=0.28,
       impairment=(0.10, 0.35), equity_shock=(-0.05, -0.01),
       duration_days=(7, 60), severity_label="basso"),

    # ---------------------------------------------------- TECHNOLOGY -----
    _t(key="ai_breakthrough", category=EventCategory.TECHNOLOGY,
       scope=EventScope.SECTOR,
       headline="Salto tecnologico nell'IA: {target} rivaluta le stime di crescita",
       annual_rate=0.24, favourable=True,
       equity_shock=(0.10, 0.45), duration_days=(180, 720),
       drift_change=(0.02, 0.06),
       sector_filter=("Semis", "Software", "Comm. Services"),
       severity_label="favorevole"),
    _t(key="tech_disruption", category=EventCategory.TECHNOLOGY,
       scope=EventScope.SECTOR,
       headline="Nuovo entrante disruptivo in {target}: margini sotto pressione",
       annual_rate=0.20,
       equity_shock=(-0.25, -0.06), duration_days=(180, 900),
       drift_change=(-0.03, -0.008), severity_label="alto"),

    # ------------------------------------------------------ COMPANY ------
    _t(key="earnings_beat", category=EventCategory.COMPANY,
       scope=EventScope.COMPANY,
       headline="{target} batte le stime: utili sopra il consenso del {pct}",
       annual_rate=6.0, favourable=True,
       equity_shock=(0.03, 0.16), duration_days=(5, 30),
       drift_change=(0.002, 0.012), severity_label="favorevole"),
    _t(key="earnings_miss", category=EventCategory.COMPANY,
       scope=EventScope.COMPANY,
       headline="{target} delude: utili sotto il consenso del {pct}",
       annual_rate=5.0,
       equity_shock=(-0.18, -0.03), duration_days=(5, 30),
       drift_change=(-0.012, -0.002), severity_label="medio"),
    _t(key="guidance_cut", category=EventCategory.COMPANY,
       scope=EventScope.COMPANY,
       headline="{target} taglia la guidance per l'esercizio in corso",
       annual_rate=1.6,
       equity_shock=(-0.22, -0.06), duration_days=(30, 120),
       drift_change=(-0.02, -0.004), distress_multiplier=1.3,
       severity_label="alto"),
    _t(key="product_breakthrough", category=EventCategory.COMPANY,
       scope=EventScope.COMPANY,
       headline="{target} annuncia un prodotto che ridefinisce la categoria",
       annual_rate=1.2, favourable=True,
       equity_shock=(0.08, 0.35), duration_days=(90, 400),
       drift_change=(0.01, 0.045), severity_label="favorevole"),
    _t(key="mna", category=EventCategory.COMPANY, scope=EventScope.COMPANY,
       headline="Operazione di M&A su {target}: premio del {pct}",
       annual_rate=0.9, favourable=True,
       equity_shock=(0.10, 0.35), duration_days=(30, 180),
       severity_label="favorevole"),
    _t(key="fraud_scandal", category=EventCategory.COMPANY,
       scope=EventScope.COMPANY,
       headline="Irregolarità contabili in {target}: indagine aperta",
       annual_rate=0.22,
       equity_shock=(-0.55, -0.20), duration_days=(90, 540),
       drift_change=(-0.04, -0.01), distress_multiplier=4.0,
       severity_label="estremo"),
    _t(key="regulatory_fine", category=EventCategory.COMPANY,
       scope=EventScope.COMPANY,
       headline="Sanzione dell'autorità su {target}: multa record",
       annual_rate=0.7,
       equity_shock=(-0.14, -0.03), duration_days=(20, 120),
       severity_label="medio"),
    _t(key="antitrust", category=EventCategory.COMPANY,
       scope=EventScope.COMPANY,
       headline="Istruttoria antitrust su {target}: ipotesi di spezzatino",
       annual_rate=0.28,
       equity_shock=(-0.20, -0.05), duration_days=(180, 900),
       drift_change=(-0.02, -0.004), severity_label="alto"),
    _t(key="ceo_change", category=EventCategory.COMPANY,
       scope=EventScope.COMPANY,
       headline="Cambio al vertice di {target}",
       annual_rate=1.1,
       equity_shock=(-0.08, 0.08), duration_days=(10, 60),
       severity_label="basso"),
    _t(key="labour_dispute", category=EventCategory.COMPANY,
       scope=EventScope.COMPANY,
       headline="Sciopero prolungato in {target}: linee produttive ferme",
       annual_rate=0.6,
       impairment=(0.15, 0.45), equity_shock=(-0.10, -0.02),
       duration_days=(10, 90), severity_label="medio"),
    _t(key="plant_accident", category=EventCategory.COMPANY,
       scope=EventScope.COMPANY,
       headline="Incidente industriale in un impianto di {target}",
       annual_rate=0.45,
       impairment=(0.20, 0.55), equity_shock=(-0.12, -0.03),
       duration_days=(30, 180), distress_multiplier=1.4,
       severity_label="alto"),
    _t(key="patent_win", category=EventCategory.COMPANY,
       scope=EventScope.COMPANY,
       headline="{target} vince la causa sui brevetti: royalties in arrivo",
       annual_rate=0.5, favourable=True,
       equity_shock=(0.04, 0.18), duration_days=(60, 300),
       drift_change=(0.004, 0.018), severity_label="favorevole"),
    _t(key="major_contract", category=EventCategory.COMPANY,
       scope=EventScope.COMPANY,
       headline="{target} si aggiudica un contratto pluriennale da record",
       annual_rate=1.4, favourable=True,
       equity_shock=(0.05, 0.22), duration_days=(90, 500),
       drift_change=(0.008, 0.03), severity_label="favorevole"),
]

# Named logistics / foundry chokepoints the CHOKEPOINT scope can target,
# mapped to the tickers whose operations they gate.
CHOKEPOINTS: dict[str, tuple[str, ...]] = {
    "Canale di Suez": ("MAERSK",),
    "Stretto di Hormuz": ("2222.SR", "XOM", "SHEL"),
    "Canale di Panama": ("MAERSK", "UNP"),
    "Stretto di Taiwan": ("TSM", "2317.TW"),
    "Stretto di Malacca": ("MAERSK", "2222.SR"),
    "Mar Rosso": ("MAERSK",),
}


def _uniform(rng: np.random.Generator, bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    return lo if lo == hi else float(rng.uniform(lo, hi))


def sample_events(
    horizon_days: int,
    *,
    seed: int = 42,
    catalogue: list[EventTemplate] | None = None,
    intensity: float = 1.0,
    countries: list[str] | None = None,
    sectors: list[str] | None = None,
    tickers: list[str] | None = None,
    regions: list[str] | None = None,
) -> list[WorldEvent]:
    """Draw a world timeline of events over `horizon_days`.

    Each template is an independent Poisson process: the count over the
    horizon is Poisson(rate * years * intensity) and arrival days are
    uniform within it. `intensity` scales every rate at once, which is how
    the UI offers a calmer or more turbulent world without rewriting the
    catalogue.

    All draws come from one seeded generator in a fixed order, so `seed`
    determines the world completely — and the world is independent of any
    portfolio, which is what lets two books be compared on one path.
    """
    if horizon_days < 1:
        raise ValueError(f"horizon_days deve essere positivo, ricevuto {horizon_days}")
    if intensity <= 0:
        raise ValueError(f"intensity deve essere positiva, ricevuta {intensity}")

    catalogue = catalogue or EVENT_CATALOGUE
    rng = np.random.default_rng(seed)
    years = horizon_days / 365.0

    countries = countries or ["US", "CN", "DE", "JP", "TW", "KR", "GB", "FR", "IT"]
    sectors = sectors or ["Semis", "Software", "Energy", "Financials", "Health Care"]
    tickers = tickers or ["AAPL", "MSFT", "NVDA"]
    regions = regions or ["Asia", "Europe", "Americas", "EMEA"]

    events: list[WorldEvent] = []
    # Sort by key so the draw order is stable regardless of catalogue order.
    for template in sorted(catalogue, key=lambda t: t.key):
        expected = template.annual_rate * years * intensity
        count = int(rng.poisson(expected))
        for _ in range(count):
            day = int(rng.integers(0, horizon_days))
            target, resolved = _resolve_target(
                rng, template, countries, sectors, tickers, regions
            )
            if not resolved:
                continue

            impairment = _uniform(rng, template.impairment)
            equity = _uniform(rng, template.equity_shock)
            market = _uniform(rng, template.market_shock)
            drift = _uniform(rng, template.drift_change)
            duration = int(rng.integers(
                template.duration_days[0], template.duration_days[1] + 1
            ))

            events.append(WorldEvent(
                day=day,
                template=template,
                target=target,
                targets_resolved=resolved,
                impairment=impairment,
                equity_shock=equity,
                market_shock=market,
                duration_days=duration,
                drift_change=drift,
                headline=_render(template, target, equity, market, rng),
            ))

    events.sort(key=lambda e: (e.day, e.template.key))
    return events


def _resolve_target(
    rng: np.random.Generator,
    template: EventTemplate,
    countries: list[str],
    sectors: list[str],
    tickers: list[str],
    regions: list[str],
) -> tuple[str, list[str]]:
    """Pick what the event hits, and return (label, resolution keys)."""
    scope = template.scope
    if scope is EventScope.GLOBAL:
        return "world", ["*"]
    if scope is EventScope.CHOKEPOINT:
        name = str(rng.choice(sorted(CHOKEPOINTS)))
        return name, list(CHOKEPOINTS[name])
    if scope is EventScope.SECTOR:
        pool = list(template.sector_filter) or sectors
        pick = str(rng.choice(sorted(pool)))
        return pick, [pick]
    if scope is EventScope.COUNTRY:
        pool = list(template.country_filter) or countries
        pick = str(rng.choice(sorted(pool)))
        return pick, [pick]
    if scope is EventScope.REGION:
        pick = str(rng.choice(sorted(regions)))
        return pick, [pick]
    # COMPANY
    pick = str(rng.choice(sorted(tickers)))
    return pick, [pick]


def _render(
    template: EventTemplate,
    target: str,
    equity: float,
    market: float,
    rng: np.random.Generator,
) -> str:
    """Fill the headline template.

    `{pct}` uses whichever shock the event actually carries, so a headline
    never quotes a number the simulation is not applying.
    """
    magnitude = equity if abs(equity) > abs(market) else market
    if magnitude == 0.0:
        magnitude = float(rng.uniform(0.01, 0.05))
    pct = f"{abs(magnitude) * 100:.0f}%"
    return template.headline.format(target=target, pct=pct)


def expected_event_market_drift(
    catalogue: list[EventTemplate] | None = None, intensity: float = 1.0
) -> float:
    """Annualised expected contribution of the catalogue to the market factor.

    Needed because the catalogue is **not** drift-neutral: disasters
    outnumber windfalls, so summed over their intensities the events carry a
    large negative expected return. Left uncompensated, every simulation
    becomes a doom spiral where the market falls regardless of the drift
    parameter, and a forward test you cannot win teaches nothing.

    The simulator subtracts this from its drift so `market_drift` means the
    *unconditional* expected return, and the correction re-derives itself
    whenever the catalogue changes.
    """
    catalogue = catalogue or EVENT_CATALOGUE
    return intensity * sum(
        t.annual_rate * (t.market_shock[0] + t.market_shock[1]) / 2.0
        for t in catalogue
    )
