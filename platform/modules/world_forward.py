"""SECTION 6 — Forward stochastic world simulation.

Runs the world forward day by day: a weighted-random event stream drives a
factor model for prices, the two-clock supply-chain physics for operational
damage, and a hazard process for bankruptcy. Companies grow, fail, and get
hit by things happening elsewhere.

**The load-bearing design choice: the world is portfolio-independent.**

Nothing in the simulated world depends on what the user holds. That buys
three things at once:

* the user can edit the portfolio *mid-run* and the P&L re-derives instantly,
  because valuation is a cheap reduction over an already-computed world;
* two portfolios can be compared on the *identical* path, which is the only
  comparison that isolates the portfolio rather than the luck;
* the heavy maths precomputes in Python and the browser just plays it back,
  which is what keeps the globe at frame rate.

Price model, per company per day:

    r = beta * market + sector_factor + idiosyncratic
        + event_jumps + impairment_drag

`market` carries the global drift, its volatility, and event market shocks.
Event equity shocks arrive as **jumps on the onset day** — equities reprice
on news — while `impairment_drag` follows the slow supply-chain channel, so
a foundry outage dents the price immediately on the headline and then grinds
earnings down for months as inventories empty. That split is the two-clock
model showing up in the P&L.

Calibration is stated, not hidden: annual drift 7%, market vol 16%, sector
vol 10%, idiosyncratic vol 24%, base bankruptcy hazard 0.4%/yr. Plausible
long-run figures, not fitted — and the first thing to revise once the
historical validation harness exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from modules.supply_chain_graph import SupplyChainGraph
from modules.world_events import (
    WorldEvent,
    expected_event_market_drift,
    sample_events,
)
from modules.world_universe import COMPANIES, COUNTRIES

__all__ = ["ForwardConfig", "WorldTimeline", "simulate_world"]

TRADING_DAYS = 252

# Sector drift/vol tilts, applied on top of the market factor. Growth sectors
# get more drift and more vol; staples and utilities less of both.
SECTOR_PROFILE: dict[str, tuple[float, float]] = {
    "Semis": (0.045, 0.20),
    "Semis Equipment": (0.040, 0.19),
    "Software": (0.035, 0.14),
    "Comm. Services": (0.025, 0.14),
    "Hardware": (0.020, 0.15),
    "Contract Mfg": (0.005, 0.16),
    "Cons. Discretionary": (0.015, 0.16),
    "Cons. Staples": (-0.005, 0.07),
    "Health Care": (0.010, 0.10),
    "Financials": (0.005, 0.13),
    "Energy": (0.000, 0.19),
    "Materials": (0.000, 0.16),
    "Industrials": (0.010, 0.12),
    "Auto Components": (0.000, 0.15),
    "Utilities": (-0.010, 0.08),
    "Real Estate": (-0.005, 0.12),
}
_DEFAULT_PROFILE = (0.010, 0.13)


@dataclass
class ForwardConfig:
    """Simulation parameters. All rates annual unless stated."""

    horizon_days: int = 365 * 3          # calendar days the user picks
    seed: int = 2026
    event_intensity: float = 1.0         # scales every event rate at once
    market_drift: float = 0.07
    market_vol: float = 0.16
    idiosyncratic_vol: float = 0.24
    base_hazard: float = 0.004           # annual bankruptcy probability
    # Ceiling on how much stress may multiply the base hazard. Without it a
    # deep-drawdown market makes even a AAA staple default, which is how a
    # first run produced a bankrupt Johnson & Johnson.
    max_hazard_multiplier: float = 4.0
    propagation_tick_days: int = 7
    transmission: float = 0.85
    recovery_per_tick: float = 0.03
    # Price sensitivity to operational impairment, per unit per year.
    impairment_price_beta: float = -0.55
    # Countries/sectors the event sampler may target.
    allow_bankruptcy: bool = True

    def __post_init__(self) -> None:
        if self.horizon_days < 30:
            raise ValueError(
                f"orizzonte troppo corto: {self.horizon_days} giorni (minimo 30)"
            )
        if self.horizon_days > 365 * 20:
            raise ValueError("orizzonte massimo 20 anni")
        if self.event_intensity <= 0:
            raise ValueError("event_intensity deve essere positiva")
        if self.market_vol < 0 or self.idiosyncratic_vol < 0:
            raise ValueError("le volatilità non possono essere negative")
        if not 0.0 <= self.base_hazard < 1.0:
            raise ValueError("base_hazard fuori range [0,1)")

    @property
    def n_steps(self) -> int:
        """Trading days simulated."""
        return max(2, int(self.horizon_days * TRADING_DAYS / 365))


@dataclass
class WorldTimeline:
    """A complete simulated world. Contains no portfolio information."""

    config: ForwardConfig
    tickers: list[str]
    # day index -> calendar day number
    calendar_days: list[int]
    # ticker -> price index path, starting at 100
    prices: dict[str, list[float]]
    # ticker -> impairment path (0..1)
    impairment: dict[str, list[float]]
    market_index: list[float]
    events: list[WorldEvent]
    # ticker -> day it went bankrupt, if it did
    bankruptcies: dict[str, int] = field(default_factory=dict)
    # ticker -> market cap path
    market_caps: dict[str, list[float]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def n_steps(self) -> int:
        return len(self.calendar_days)

    def portfolio_path(self, weights: dict[str, float]) -> list[float]:
        """Cumulative return path for `weights`, rebalanced never.

        A bankrupt name's price path already goes to its recovery value, so
        the loss is carried without special-casing here. Names absent from
        the world are held flat and reported by `uncovered_weight`.
        """
        total = sum(abs(w) for w in weights.values())
        if total <= 0:
            raise ValueError("i pesi di portafoglio sommano a zero")

        held = {t: w / total for t, w in weights.items() if t in self.prices}
        cash = 1.0 - sum(held.values())

        path: list[float] = []
        for i in range(self.n_steps):
            value = cash
            for ticker, w in held.items():
                value += w * (self.prices[ticker][i] / 100.0)
            path.append(value - 1.0)
        return path

    def uncovered_weight(self, weights: dict[str, float]) -> float:
        total = sum(abs(w) for w in weights.values())
        if total <= 0:
            return 0.0
        return sum(
            abs(w) for t, w in weights.items() if t not in self.prices
        ) / total

    def news_for(
        self, weights: dict[str, float] | None = None
    ) -> list[dict]:
        """Every event, flagged for whether it touches the portfolio.

        Returns the whole world's news, not a filtered feed: the point of a
        forward test is that things happen elsewhere and reach you through
        the graph, so hiding non-portfolio events would hide the mechanism.
        """
        held = set(weights or {})
        out: list[dict] = []
        for ev in self.events:
            payload = ev.to_dict()
            direct = held & set(ev.targets_resolved)
            payload["portfolioRelevance"] = (
                "diretta" if direct
                else "indiretta" if self._reaches_portfolio(ev, held)
                else "mondo"
            )
            payload["portfolioTargets"] = sorted(direct)
            out.append(payload)
        return out

    def _reaches_portfolio(self, ev: WorldEvent, held: set[str]) -> bool:
        """Does this event touch a held name through scope or the graph?"""
        if not held:
            return False
        if ev.template.scope.value in ("global", "sector", "country", "region"):
            return True
        return bool(held & set(ev.targets_resolved))

    def country_impairment(self, step: int) -> dict[str, float]:
        """Cap-weighted impairment by revenue geography at `step`."""
        weighted: dict[str, float] = {}
        totals: dict[str, float] = {}
        by_ticker = {c.ticker: c for c in COMPANIES}
        for ticker in self.tickers:
            spec = by_ticker.get(ticker)
            if spec is None:
                continue
            cap = self.market_caps.get(ticker, [spec.market_cap_usd])[
                min(step, len(self.market_caps.get(ticker, [1])) - 1)
            ]
            imp = self.impairment[ticker][step]
            for country, share in spec.revenue_geography.items():
                w = cap * share
                weighted[country] = weighted.get(country, 0.0) + imp * w
                totals[country] = totals.get(country, 0.0) + w
        return {
            c: (weighted[c] / totals[c] if totals[c] > 0 else 0.0)
            for c in weighted
        }


def simulate_world(
    graph: SupplyChainGraph,
    config: ForwardConfig | None = None,
) -> WorldTimeline:
    """Run the forward simulation. Produces a portfolio-independent world."""
    config = config or ForwardConfig()
    rng = np.random.default_rng(config.seed)

    by_ticker = {c.ticker: c for c in COMPANIES}
    tickers = [t for t in graph.g.nodes if t in by_ticker]
    if not tickers:
        raise ValueError("il grafo non contiene aziende del dataset mondiale")

    n = config.n_steps
    dt = 1.0 / TRADING_DAYS
    sqrt_dt = np.sqrt(dt)

    # The event catalogue carries a large negative expected market return
    # (disasters outnumber windfalls), so the raw drift is offset by it. This
    # makes `market_drift` the unconditional expectation rather than a
    # number the events immediately overwhelm.
    event_drag = expected_event_market_drift(intensity=config.event_intensity)
    effective_drift = config.market_drift - event_drag

    # Events are drawn over calendar days, then mapped onto trading steps.
    events = sample_events(
        config.horizon_days,
        seed=config.seed,
        intensity=config.event_intensity,
        countries=sorted({c.domicile for c in COMPANIES}),
        sectors=sorted({c.sector for c in COMPANIES}),
        tickers=sorted(tickers),
        regions=sorted({c.region for c in COUNTRIES.values()}),
    )
    # max(1, ...): step 0 is the recorded initial state, so a day-0 event
    # lands on the first evolved step rather than being silently dropped.
    day_to_step = lambda d: min(n - 1, max(1, int(d * TRADING_DAYS / 365)))  # noqa: E731

    # --- per-ticker state -------------------------------------------------
    price = {t: 100.0 for t in tickers}
    caps = {t: by_ticker[t].market_cap_usd for t in tickers}
    drift_extra = {t: 0.0 for t in tickers}
    impairment = {t: 0.0 for t in tickers}
    buffers = {t: graph.node(t).inventory_days for t in tickers}
    dead: set[str] = set()
    bankruptcies: dict[str, int] = {}
    peak = {t: 100.0 for t in tickers}

    price_paths: dict[str, list[float]] = {t: [] for t in tickers}
    imp_paths: dict[str, list[float]] = {t: [] for t in tickers}
    cap_paths: dict[str, list[float]] = {t: [] for t in tickers}
    market_index: list[float] = []
    market_level = 100.0
    calendar_days: list[int] = []

    # --- index events onto steps -----------------------------------------
    #
    # Macro shocks are SPREAD over the event's duration; company shocks are
    # applied as same-day JUMPS. That asymmetry is deliberate and it is the
    # two-clock idea again: an earnings miss really does gap the stock in one
    # session, while a recession's -17% materialises over quarters. Applying
    # macro shocks as single-day jumps also compounds multiplicatively into
    # an implausible right tail — a first run put the 90th percentile of
    # 5-year market returns at +209%.
    jumps_market: dict[int, float] = {}
    jumps_equity: dict[int, list[tuple[WorldEvent, float]]] = {}
    for ev in events:
        step = day_to_step(ev.day)
        if ev.market_shock:
            # Spread across the event window, capped so a multi-year event
            # still has visible onset rather than vanishing into the drift.
            span_steps = max(
                1, min(int(ev.duration_days * TRADING_DAYS / 365), 180)
            )
            per_step = ev.market_shock / span_steps
            for k in range(span_steps):
                s_idx = step + k
                if s_idx >= n:
                    break
                jumps_market[s_idx] = jumps_market.get(s_idx, 0.0) + per_step
        if ev.equity_shock or ev.drift_change or ev.impairment:
            jumps_equity.setdefault(step, []).append((ev, ev.equity_shock))

    active: list[WorldEvent] = []
    tick_every = max(1, int(config.propagation_tick_days * TRADING_DAYS / 365))

    # Record the t=0 state before any evolution, so index i is the state *at*
    # day i and every path starts at the 100 baseline. Appending only after
    # evolving would bake the first day's return into the starting value, and
    # a portfolio would open at a non-zero return.
    calendar_days.append(0)
    market_index.append(market_level)
    for t in tickers:
        price_paths[t].append(price[t])
        imp_paths[t].append(0.0)
        cap_paths[t].append(caps[t])

    for step in range(1, n):
        calendar_day = int(step * 365 / TRADING_DAYS)
        calendar_days.append(calendar_day)

        # --- refresh the active event set -------------------------------
        active = [e for e in events if e.active_on(calendar_day)]
        distress = 1.0
        for e in active:
            distress = max(distress, e.template.distress_multiplier)

        # --- market factor ----------------------------------------------
        market_ret = (
            effective_drift * dt
            + config.market_vol * sqrt_dt * float(rng.standard_normal())
            + jumps_market.get(step, 0.0)
        )
        market_level *= 1.0 + market_ret
        market_index.append(market_level)

        # --- sector factors (one draw per sector per step) ---------------
        sector_ret: dict[str, float] = {}
        for sector in sorted({by_ticker[t].sector for t in tickers}):
            mu_s, vol_s = SECTOR_PROFILE.get(sector, _DEFAULT_PROFILE)
            sector_ret[sector] = (
                mu_s * dt + vol_s * sqrt_dt * float(rng.standard_normal())
            )

        # --- supply-chain physics, on its own slower clock --------------
        if (step - 1) % tick_every == 0:
            clamped = {}
            for e in active:
                if e.impairment <= 0:
                    continue
                for target in _affected_tickers(e, tickers, by_ticker):
                    clamped[target] = max(clamped.get(target, 0.0), e.impairment)
            impairment = graph.propagation_step(
                impairment, buffers,
                transmission=config.transmission,
                recovery_per_tick=config.recovery_per_tick,
                tick_days=config.propagation_tick_days,
                clamped=clamped,
                dead=dead,
            )
            # Buffers refill slowly once pressure is gone, so a company that
            # survived one shock is not permanently defenceless.
            for t in tickers:
                if impairment.get(t, 0.0) < 1e-6:
                    buffers[t] = min(
                        graph.node(t).inventory_days,
                        buffers[t] + config.propagation_tick_days * 0.5,
                    )

        # --- per-company returns ----------------------------------------
        equity_jumps: dict[str, float] = {}
        for ev, shock in jumps_equity.get(step, []):
            for target in _affected_tickers(ev, tickers, by_ticker):
                equity_jumps[target] = equity_jumps.get(target, 0.0) + shock
                drift_extra[target] += ev.drift_change

        for t in tickers:
            if t in dead:
                price_paths[t].append(price[t])
                imp_paths[t].append(1.0)
                cap_paths[t].append(caps[t])
                continue

            node = graph.node(t)
            spec = by_ticker[t]
            r = (
                node.beta * market_ret
                + sector_ret[spec.sector]
                + drift_extra[t] * dt
                + config.idiosyncratic_vol * sqrt_dt * float(rng.standard_normal())
                + equity_jumps.get(t, 0.0)
                + config.impairment_price_beta * impairment.get(t, 0.0) * dt
            )
            price[t] = max(0.01, price[t] * (1.0 + r))
            caps[t] = max(1e6, caps[t] * (1.0 + r))
            peak[t] = max(peak[t], price[t])

            # --- bankruptcy hazard --------------------------------------
            if config.allow_bankruptcy:
                drawdown = 1.0 - price[t] / peak[t] if peak[t] > 0 else 0.0
                # Hazard rises with drawdown and sustained operational damage;
                # a healthy company at its high is near the base rate.
                stress = (
                    1.0
                    + 2.0 * max(0.0, drawdown - 0.65)
                    + 1.2 * impairment.get(t, 0.0)
                )
                # distress enters as a damped root rather than a raw product:
                # several overlapping long events would otherwise keep the
                # multiplier pinned high for years.
                multiplier = min(
                    np.sqrt(distress) * stress, config.max_hazard_multiplier
                )
                hazard = config.base_hazard * dt * multiplier
                if float(rng.random()) < hazard:
                    dead.add(t)
                    bankruptcies[t] = calendar_day
                    # Equity recovery in liquidation: a few cents on the euro.
                    price[t] = price[t] * float(rng.uniform(0.02, 0.15))

            price_paths[t].append(price[t])
            imp_paths[t].append(impairment.get(t, 0.0))
            cap_paths[t].append(caps[t])

    warnings: list[str] = []
    if graph.observed_share < 0.5:
        warnings.append(
            f"Solo il {graph.observed_share:.0%} degli archi è osservato in "
            "filing: la propagazione di secondo ordine è indicativa."
        )
    warnings.append(
        "Simulazione non validata su episodi storici: serve per test "
        "forward-looking comparativi, non come previsione."
    )
    warnings.append(
        f"Drift di mercato: {config.market_drift:.1%} atteso incondizionato "
        f"(compensati {event_drag:+.2%}/anno di contributo netto degli eventi)."
    )

    return WorldTimeline(
        config=config,
        tickers=tickers,
        calendar_days=calendar_days,
        prices=price_paths,
        impairment=imp_paths,
        market_index=market_index,
        events=events,
        bankruptcies=bankruptcies,
        market_caps=cap_paths,
        warnings=warnings,
    )


def _affected_tickers(
    ev: WorldEvent, tickers: list[str], by_ticker: dict
) -> list[str]:
    """Resolve an event's scope onto concrete tickers."""
    scope = ev.template.scope.value
    if scope == "global":
        return list(tickers)
    if scope in ("company", "chokepoint"):
        return [t for t in ev.targets_resolved if t in by_ticker]
    if scope == "sector":
        wanted = set(ev.targets_resolved)
        return [t for t in tickers if by_ticker[t].sector in wanted]
    if scope == "country":
        wanted = set(ev.targets_resolved)
        # Revenue geography, not domicile: a country shock hits whoever
        # actually sells there.
        return [
            t for t in tickers
            if any(c in wanted for c in by_ticker[t].revenue_geography)
            or by_ticker[t].domicile in wanted
        ]
    if scope == "region":
        wanted = set(ev.targets_resolved)
        codes = {c.code for c in COUNTRIES.values() if c.region in wanted}
        return [
            t for t in tickers
            if any(c in codes for c in by_ticker[t].revenue_geography)
            or by_ticker[t].domicile in codes
        ]
    return []
