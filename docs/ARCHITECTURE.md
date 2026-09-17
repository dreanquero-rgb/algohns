# Algohns V12 — Architecture & Repository Map

Algohns V12 is a **glue-code platform**: it does not reinvent quant primitives,
it *integrates* the best open-source financial libraries into one coherent
Streamlit application. This document is the map from the requested feature set
to the concrete code and the upstream repositories each module leans on.

---

## 1. Recommended GitHub repository map (per module)

Legend: ✅ = wired in / used · 🔎 = evaluated, reference-only · ❌ = evaluated, not used.

### Module 1 — European Bond Yield & Multi-Tax Engine → `bond_engine.py`
| Repo / library | Role | Status |
|---|---|---|
| `lballabio/QuantLib` + `QuantLib-Python` | Reference bond math (YTM, duration, convexity) — used as **independent cross-check** | ✅ |
| `scipy` (`optimize.brentq`) | Precise TIR/XIRR yield solving (net & gross) | ✅ |
| `ranaroussi/yfinance` | Market prices for bond-ETF proxies / context | ✅ |
| `Librefolio/borsaItaliana-scraping` | Borsa Italiana price scraping pattern (BTP quotes) | 🔎 reference for a scraping adapter |
| `hello245m/free-stockdb` | Free ticker/price DB | 🔎 optional data source |
| `OpenBB-finance/OpenBB` | Alternative market-data aggregator | 🔎 drop-in for `data_providers` |

> The Italian/EU taxation logic (12.5% white-list vs 26% corporate, *disaggio
> d'emissione*, *minusvalenze* compensation) is proprietary to Algohns and lives
> in `TaxProfile` / `BondEngine` — no upstream repo covers it.

### Module 2 — Alpaca Asynchronous Engine → `alpaca_execution.py`, `workers/`
| Repo / library | Role | Status |
|---|---|---|
| `alpacahq/alpaca-py` | Official trading SDK (paper) | ✅ |
| `celery/celery` + `redis` | Async background execution & scheduling | ✅ |
| `agronholm/apscheduler` | Broker-less scheduler fallback | ✅ |
| `alpacahq/alpaca-backtrader-api` | Alpaca↔Backtrader bridge | 🔎 optional live-strategy bridge |
| `kay-ou/SimTradeDesk` | Trade-desk UX inspiration | 🔎 reference |

### Module 3 — Backtesting & Portfolio Optimization → `backtest_suite.py`
| Repo / library | Role | Status |
|---|---|---|
| `robertmartin8/PyPortfolioOpt` | Max Sharpe / Min-Var / Black-Litterman | ✅ |
| `pmorissette/ffn` | Extended performance stats (cross-check) | ✅ |
| `polakowo/vectorbt` | Vectorised backtesting | 🔎 optional (heavy) |
| `mementum/backtrader` | Event-driven engine | 🔎 optional |
| `dppalomar/portfolioBacktest` (R) | Rolling-window backtest methodology | 🔎 reference (R, not wired) |
| `letianzj/QuantResearch` | Strategy/notebook reference | 🔎 reference |
| `PythonForForex/Backtrader-for-backtesting` | Backtrader recipes | 🔎 reference |

### Module 4 — S&P 500 Supply Chain Graph → `supply_chain_graph.py`
| Repo / library | Role | Status |
|---|---|---|
| `jadchaar/sec-edgar-downloader` | Download 10-K / 10-Q filings | ✅ |
| `data.sec.gov` REST API | Filing/text fallback when the package is absent | ✅ |
| `explosion/spaCy` (`en_core_web_sm`) | ORG entity recognition | ✅ |
| `networkx/networkx` | Directed supply-chain graph & contagion metrics | ✅ |
| `WestHealth/pyvis` | Interactive HTML graph | ✅ |
| `dgunning/edgartools` | Alternative filing parser | 🔎 swappable ingestor |

### Module 5 — Consolidated SEC Financial Statements → `sec_aggregator.py`
| Repo / library | Role | Status |
|---|---|---|
| `data.sec.gov/api/xbrl/companyfacts` | Standardised XBRL financials | ✅ |
| `JerBouma/FinanceToolkit` | Ratio definitions / methodology reference | 🔎 reference |
| `JerBouma/FinanceDatabase` | Ticker/entity metadata | 🔎 optional universe source |
| `dgunning/edgartools` | XBRL statement extraction | 🔎 alternative extractor |

### Data infrastructure (evaluated, not required for the default deploy)
| Repo | Verdict |
|---|---|
| `taosdata/TDengine` | ❌ Not wired — time-series DB is overkill for the parquet cache; documented as the scale-out option for tick storage. |
| `timescale/timescaledb` | ❌ Same — recommended if/when intraday history outgrows parquet. |

---

## 2. Project structure

```
algohns/                         # repository root
├── app.py                       # Streamlit orchestrator (st.navigation multipage)
├── requirements.txt
├── .env.example                 # configuration template (copy to .env)
├── Dockerfile
├── docker-compose.yml           # dashboard + redis + celery worker + beat
├── .streamlit/config.toml       # brand theme
├── docs/
│   └── ARCHITECTURE.md          # this file
└── algohns/                     # the Python package
    ├── __init__.py
    ├── ui.py                    # shared Streamlit theme/helpers
    ├── config/
    │   └── settings.py          # env-driven settings (secrets never hard-coded)
    ├── core/
    │   ├── utils.py             # optional-dependency guards, helpers
    │   └── data_providers.py    # yfinance market-data layer + disk cache
    ├── modules/
    │   ├── bond_engine.py       # MODULE 1
    │   ├── alpaca_execution.py  # MODULE 2
    │   ├── backtest_suite.py    # MODULE 3
    │   ├── supply_chain_graph.py# MODULE 4
    │   └── sec_aggregator.py    # MODULE 5
    ├── workers/
    │   ├── celery_app.py        # Celery factory
    │   └── tasks.py             # async tasks + APScheduler fallback
    ├── app_pages/               # one Streamlit page per module
    │   ├── 1_bond_engine.py
    │   ├── 2_auto_trading.py
    │   ├── 3_backtest_suite.py
    │   ├── 4_supply_chain.py
    │   └── 5_sec_aggregator.py
    └── data/cache/              # parquet market-data cache (git-ignored)
```

The legacy Algohns V11 Cloudflare Worker (`_worker.js`, `public/`, `wrangler.toml`)
is retained at the root for reference and is independent of the Python platform.

---

## 3. Design principles

1. **Glue, don't rebuild.** Each module wraps upstream libraries behind a small,
   stable façade so any upstream can be swapped without touching the UI.
2. **Graceful degradation.** Heavy extras (QuantLib, spaCy, PyPortfolioOpt,
   Celery, pyvis) are imported lazily via `core.utils.lazy_import`; a missing
   extra produces an actionable "pip install …" message instead of a crash.
3. **Paper-only safety.** `AlpacaExecutionEngine` refuses to construct a
   non-paper client — the V11 real-money lock carries over.
4. **Secrets via env.** All credentials come from environment variables / `.env`;
   nothing sensitive is committed. `Settings.masked()` powers a safe config view.
5. **Cache for speed.** Market data is cached to parquet with a TTL so the
   dashboard stays responsive.

---

## Module 6 — World Simulation

Forward-looking stochastic simulation over the supply-chain graph, with an
orthographic globe as its display surface.

| File | Role |
|---|---|
| `algohns/modules/world_universe.py` | 54 real companies, **operating-centre** coordinates, revenue geography, 59 supply links |
| `algohns/modules/contagion.py` | Two-clock propagation physics: inventory buffers, substitutability, provenance-aware edges |
| `algohns/modules/world_events.py` | 49 event templates as independent Poisson processes |
| `algohns/modules/world_forward.py` | Factor model + propagation + bankruptcy hazard |
| `algohns/modules/world_export.py` | Compact payload for the browser |
| `public/world/index.html` | Canvas globe with its own copy of the engine |
| `algohns/app_pages/6_world_simulation.py` | Globe embed + multi-seed analysis |

`contagion.py` is separate from `supply_chain_graph.py` on purpose: that
module *mines* filings and renders them, this one *propagates shocks*
through a graph you already have. Different responsibilities.

### The choice that carries the weight: the world is portfolio-independent

Nothing in the simulated world depends on what you hold. That buys three
things at once:

1. the portfolio is editable **mid-run** and the P&L re-derives instantly,
   because valuation is a cheap reduction over an already-computed world;
2. two portfolios are comparable on the **identical** path — the only
   comparison that isolates the portfolio rather than the luck;
3. the heavy maths precomputes in Python while the browser replays, which is
   what keeps the globe at frame rate.

### Two clocks, in the P&L this time

Macro shocks are **spread** across the event window; company shocks **jump**
on the day. An earnings miss gaps the stock in one session; a recession's
-17% materialises over quarters. Applying macro shocks as daily jumps
compounded multiplicatively into an implausible right tail — a first run put
the 90th percentile of 5-year market returns at **+209%**.

### The catalogue is not drift-neutral

Disasters outnumber windfalls, so summed over their intensities the events
carried **-8.68%/yr** of expected market return. Left uncompensated every
world falls regardless of the drift parameter, and a forward test you cannot
win teaches nothing. `expected_event_market_drift()` derives that
contribution *from the catalogue* and the simulator subtracts it, so
`market_drift` is the unconditional expectation and the correction
re-derives itself whenever the catalogue changes.

### Calibration measured, not asserted

Across 40-150 seeds: realised volatility **16.0%** against the 16%
parameter; 5-year median **+32.8%** against the **+32.1%** implied by the
*geometric* drift (7% - 16%²/2). Comparing against the arithmetic drift
compounded (+40.3%) made a correct calibration look broken — the benchmark
was wrong, not the model. Bankruptcies land around 1.5x the base rate, which
is what stressed large caps should do.

### Two stated limits

**The engine exists twice.** Python is the reference implementation and the
tested one; the browser copy is the interactive preview. Two implementations
of one model drift apart. Reconciling them — ideally by having the JS read a
golden path produced by Python in CI — is outstanding work, and until it
exists the browser numbers are indicative.

**No historical validation.** The propagation is internally consistent and
now calibrated, but it has never replayed COVID or the 2022 rate shock. That
harness is the real next step, and until it exists the second-order numbers
are indicative rather than predictive.

---

## Two engine fixes found by porting the tests

**Bond conventions.** The engine discounted on a calendar ACT/365 axis with
`(1 + y)^t`, which put a 4% semi-annual par bond at **4.0381%**, a 6% one at
**6.0875%** and a 5% quarterly one at **5.0924%**. Both conventions were
wrong: the time axis is now in coupon periods (ICMA) and discounting is
nominal, compounded at the coupon frequency. Par bonds now return their
coupon rate to machine precision, and the BTP figures agree with an
independent implementation. `tests/test_bond_conventions.py` pins it.

**Look-ahead bias.** Page 3 fitted weights on the whole price history with
`PortfolioOptimizer(prices)` and then "backtested" them on that same
history. The curve was in-sample: the return you would have earned knowing
the optimal weights in advance. `Backtester.run_walk_forward()` re-optimises
at each rebalance using only data available then, charges turnover costs,
and is now the page default; the in-sample path is still available but
labelled. `tests/test_backtest_causality.py` proves causality by perturbing
the tail of the history and asserting earlier returns are bit-identical.
