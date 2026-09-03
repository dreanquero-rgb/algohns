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
