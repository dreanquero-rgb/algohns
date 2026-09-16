"""Compact JSON export of a simulated world, for the browser renderer.

Size discipline matters here: 54 companies over 10 years of trading days is
~136k price points, and a naive dump of full-precision floats inside
per-day objects runs to several megabytes. Three choices keep it small:

* **Columnar, not row-wise.** One array per ticker instead of one object
  per day. Removes ~50 bytes of repeated key names per point.
* **Rounded.** Prices to 2dp, impairment to 3dp. No display needs more.
* **Sparse where it can be.** Impairment is zero for most names most of the
  time, so it ships as run-length pairs rather than a dense array.

The payload also carries the calibration and the non-validation warning, so
a renderer cannot present the numbers without the caveats travelling with
them.
"""
from __future__ import annotations

import json
from pathlib import Path

from modules.world_events import EVENT_CATALOGUE, expected_event_market_drift
from modules.world_forward import ForwardConfig, WorldTimeline, simulate_world
from modules.world_simulation import build_world
from modules.world_universe import COMPANIES, COUNTRIES

__all__ = ["timeline_to_payload", "write_world_payload", "DEFAULT_PORTFOLIO"]

DEFAULT_PORTFOLIO: dict[str, float] = {
    "AAPL": 0.12, "MSFT": 0.12, "NVDA": 0.10, "GOOGL": 0.08,
    "AMZN": 0.07, "TSM": 0.06, "ASML": 0.05, "TSLA": 0.04,
    "JPM": 0.07, "JNJ": 0.06, "LLY": 0.05, "XOM": 0.05,
    "NESN.SW": 0.04, "SIE.DE": 0.04, "KO": 0.03, "ENEL.MI": 0.02,
}


def _rle(values: list[float], places: int = 3) -> list[list[float]]:
    """Run-length encode a mostly-constant series as [value, count] pairs.

    Impairment is zero for most companies most of the time, so this cuts the
    largest array in the payload by one to two orders of magnitude.
    """
    out: list[list[float]] = []
    for v in values:
        r = round(v, places)
        if out and out[-1][0] == r:
            out[-1][1] += 1
        else:
            out.append([r, 1])
    return out


def timeline_to_payload(
    timeline: WorldTimeline,
    *,
    portfolio: dict[str, float] | None = None,
) -> dict:
    """Shape a simulated world for the renderer."""
    portfolio = portfolio if portfolio is not None else DEFAULT_PORTFOLIO
    by_ticker = {c.ticker: c for c in COMPANIES}
    graph = build_world()

    importance = graph.systemic_importance()
    peak = max(importance.values()) if importance else 1.0

    nodes = []
    for ticker in timeline.tickers:
        spec = by_ticker[ticker]
        node = graph.node(ticker)
        nodes.append({
            "id": ticker,
            "name": spec.name,
            "sector": spec.sector,
            "domicile": spec.domicile,
            "lat": spec.lat,
            "lon": spec.lon,
            "cap0": spec.market_cap_usd,
            "beta": node.beta,
            "inventoryDays": node.inventory_days,
            "substitutability": node.substitutability,
            "revenueGeography": node.revenue_geography,
            "primaryCountry": node.primary_country,
            "systemic": round(
                importance.get(ticker, 0.0) / peak if peak else 0.0, 4
            ),
            # Columnar paths.
            "px": [round(v, 2) for v in timeline.prices[ticker]],
            "imp": _rle(timeline.impairment[ticker]),
            "failedDay": timeline.bankruptcies.get(ticker),
        })

    edges = [
        {
            "s": e.source, "t": e.target,
            "d": round(e.dependence, 3),
            "o": e.provenance.is_observed,
            "sLat": by_ticker[e.source].lat, "sLon": by_ticker[e.source].lon,
            "tLat": by_ticker[e.target].lat, "tLon": by_ticker[e.target].lon,
        }
        for e in graph.edges()
        if e.source in by_ticker and e.target in by_ticker
    ]

    cfg = timeline.config
    drag = expected_event_market_drift(intensity=cfg.event_intensity)

    return {
        "meta": {
            "generator": "algohns-world-simulation",
            "seed": cfg.seed,
            "horizonDays": cfg.horizon_days,
            "steps": timeline.n_steps,
            "tradingDaysPerYear": 252,
            "eventIntensity": cfg.event_intensity,
            "calibration": {
                "marketDrift": cfg.market_drift,
                "marketVol": cfg.market_vol,
                "idiosyncraticVol": cfg.idiosyncratic_vol,
                "baseHazardAnnual": cfg.base_hazard,
                "eventDriftCompensated": round(drag, 5),
            },
            "observedEdgeShare": round(graph.observed_share, 4),
            "eventTemplates": len(EVENT_CATALOGUE),
            "favourableTemplateShare": round(
                sum(1 for t in EVENT_CATALOGUE if t.favourable)
                / len(EVENT_CATALOGUE), 3
            ),
            "warnings": timeline.warnings,
            "dataProvenance": (
                "Dataset curato a mano: sedi operative e forma della "
                "geografia dei ricavi da conoscenza pubblica, percentuali "
                "di dipendenza come assunzioni di modello."
            ),
        },
        "countries": [
            {"code": c.code, "name": c.name, "lat": c.lat, "lon": c.lon,
             "region": c.region}
            for c in COUNTRIES.values()
        ],
        "calendarDays": timeline.calendar_days,
        "marketIndex": [round(v, 2) for v in timeline.market_index],
        "nodes": nodes,
        "edges": edges,
        "events": timeline.news_for(portfolio),
        "defaultPortfolio": portfolio,
    }


def write_world_payload(
    path: str | Path,
    *,
    config: ForwardConfig | None = None,
    portfolio: dict[str, float] | None = None,
) -> dict:
    """Simulate a world and write its payload to `path`."""
    graph = build_world()
    timeline = simulate_world(graph, config or ForwardConfig())
    payload = timeline_to_payload(timeline, portfolio=portfolio)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, separators=(",", ":")))
    return payload


def export_static_world() -> dict:
    """The world's *geometry and rules*, without any simulated path.

    Shipped to the browser so the renderer can run its own worlds on demand
    — press re-roll and get a new seed without a round trip to Python.

    The honest trade-off: this means two implementations of the same price
    model, and two implementations drift. Python is the reference — it is
    what the 59 tests in `test_world_simulation.py` exercise and what the
    calibration figures come from — and the browser copy is the interactive
    preview. Reconciling them (ideally by having the JS read a golden path
    from Python in CI) is follow-up work, and until it exists the browser
    numbers should be read as indicative.
    """
    from modules.world_events import CHOKEPOINTS, EVENT_CATALOGUE
    from modules.world_forward import SECTOR_PROFILE, ForwardConfig
    from modules.world_universe import SUPPLY_LINKS

    graph = build_world()
    importance = graph.systemic_importance()
    peak = max(importance.values()) if importance else 1.0
    by_ticker = {c.ticker: c for c in COMPANIES}
    defaults = ForwardConfig()

    return {
        "meta": {
            "generator": "algohns-world-simulation/static",
            "observedEdgeShare": round(graph.observed_share, 4),
            "tradingDaysPerYear": 252,
            "eventDriftCompensation": round(expected_event_market_drift(), 6),
            "calibration": {
                "marketDrift": defaults.market_drift,
                "marketVol": defaults.market_vol,
                "idiosyncraticVol": defaults.idiosyncratic_vol,
                "baseHazard": defaults.base_hazard,
                "maxHazardMultiplier": defaults.max_hazard_multiplier,
                "transmission": defaults.transmission,
                "recoveryPerTick": defaults.recovery_per_tick,
                "propagationTickDays": defaults.propagation_tick_days,
                "impairmentPriceBeta": defaults.impairment_price_beta,
            },
            "provenance": (
                "Dataset curato a mano: sedi operative e forma della geografia "
                "dei ricavi da conoscenza pubblica; le percentuali di "
                "dipendenza sono assunzioni di modello, non dati dichiarati."
            ),
            "notValidated": (
                "Il modello di propagazione non e' stato confrontato con "
                "episodi storici. Serve per confronti forward-looking fra "
                "portafogli sullo stesso mondo, non come previsione."
            ),
            "referenceImplementation": (
                "Python e' l'implementazione di riferimento e testata; questa "
                "copia nel browser e' l'anteprima interattiva."
            ),
        },
        "countries": [
            {"code": c.code, "name": c.name, "lat": c.lat, "lon": c.lon,
             "region": c.region}
            for c in COUNTRIES.values()
        ],
        "sectorProfile": {
            k: {"drift": v[0], "vol": v[1]} for k, v in SECTOR_PROFILE.items()
        },
        "chokepoints": {k: list(v) for k, v in CHOKEPOINTS.items()},
        "nodes": [
            {
                "id": c.ticker, "name": c.name, "sector": c.sector,
                "domicile": c.domicile, "lat": c.lat, "lon": c.lon,
                "cap": c.market_cap_usd, "beta": c.beta,
                "inv": c.inventory_days, "sub": c.substitutability,
                "geo": c.revenue_geography,
                "primaryCountry": graph.node(c.ticker).primary_country,
                "systemic": round(
                    importance.get(c.ticker, 0.0) / peak if peak else 0.0, 4
                ),
            }
            for c in COMPANIES
        ],
        "edges": [
            {
                "s": s, "t": t, "d": round(d, 3), "o": o,
                "sLat": by_ticker[s].lat, "sLon": by_ticker[s].lon,
                "tLat": by_ticker[t].lat, "tLon": by_ticker[t].lon,
            }
            for s, t, d, o in SUPPLY_LINKS
        ],
        "eventCatalogue": [
            {
                "key": t.key, "category": t.category.value,
                "scope": t.scope.value, "headline": t.headline,
                "rate": t.annual_rate,
                "imp": list(t.impairment), "eq": list(t.equity_shock),
                "mkt": list(t.market_shock), "dur": list(t.duration_days),
                "drift": list(t.drift_change),
                "distress": t.distress_multiplier,
                "fav": t.favourable, "sev": t.severity_label,
                "sectors": list(t.sector_filter),
                "countries": list(t.country_filter),
            }
            for t in EVENT_CATALOGUE
        ],
        "defaultPortfolio": DEFAULT_PORTFOLIO,
    }
