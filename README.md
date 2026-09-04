# Algohns V12 — Quant Asset Manager OS (Python)

Algohns V12 is a modular **quant asset-management platform** that glues
best-in-class open-source financial libraries into a single, high-performance
**Streamlit** dashboard. It is the Python successor to the Algohns V11 Cloudflare
Worker, extending it from Alpaca paper-trading into a five-module quant OS.

> **Paper trading only.** Real-money execution is locked platform-wide, exactly
> as in V11. The Alpaca engine refuses to construct a non-paper client.

---

## The five modules

| # | Module | File | What it does |
|---|--------|------|--------------|
| 1 | **European Bond Yield & Multi-Tax Engine** | `algohns/modules/bond_engine.py` | Net YTM (TIR/XIRR), accrued interest, Macaulay/Modified Duration, Convexity + dynamic taxation (IT 12.5% white-list vs 26% corporate, *disaggio d'emissione*, *minusvalenze*). QuantLib cross-check. |
| 2 | **Alpaca Asynchronous Engine** | `algohns/modules/alpaca_execution.py` + `algohns/workers/` | Paper order execution, portfolio sync, rebalancing; background workers via Celery/Redis or APScheduler (runs with the browser closed). |
| 3 | **Backtesting & Portfolio Optimization** | `algohns/modules/backtest_suite.py` | Max Sharpe / Min-Variance / Risk Parity / Black-Litterman (PyPortfolioOpt) + full risk metrics (Sharpe, Sortino, Calmar, Max Drawdown, Alpha, Beta, VaR/CVaR). |
| 4 | **S&P 500 Supply Chain Graph** | `algohns/modules/supply_chain_graph.py` | Mines 10-K/10-Q filings (sec-edgar-downloader / EDGAR API), extracts supplier–customer links (spaCy + RegEx), builds a directed graph (NetworkX) with contagion metrics and an interactive PyVis view. |
| 5 | **Consolidated SEC Financial Statements** | `algohns/modules/sec_aggregator.py` | Pulls XBRL company facts from `data.sec.gov`, normalises Income Statement / Balance Sheet / Cash Flow, compares tickers side-by-side with key ratios. |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full **repository map**
(which upstream repos feed each module) and design rationale.

---

## Quick start

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm      # for Module 4 NER (optional)

# 2. Configure
cp .env.example .env                          # add Alpaca paper keys, SEC user-agent

# 3. Run the dashboard
streamlit run app.py
```

Open http://localhost:8501.

### Background auto-trading (Module 2)

```bash
# Production: Celery + Redis
celery -A algohns.workers.celery_app.app worker --loglevel=info
celery -A algohns.workers.celery_app.app beat   --loglevel=info

# Laptop: no broker required
python -c "from algohns.workers.tasks import InlineScheduler; s=InlineScheduler(); s.start()"
```

### Docker (full stack)

```bash
docker compose up --build        # dashboard + redis + worker + beat
```

### Deploy (Streamlit Cloud + Cloudflare redirect)

The dashboard runs on **Streamlit Community Cloud** (or any Python host); the
Cloudflare Worker at `algohns.dreanquero.workers.dev` redirects to it via the `APP_URL`
var. Full step-by-step in [`docs/DEPLOY.md`](docs/DEPLOY.md).

---

## Configuration

All settings come from environment variables / `.env` (never hard-coded):

| Variable | Purpose |
|---|---|
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | Alpaca **paper** credentials (Module 2) |
| `ALPACA_PAPER` | Must stay `true` (real-money is locked) |
| `REDIS_URL` | Celery broker/backend (Module 2) |
| `SEC_USER_AGENT` | `Name email@example.com` — required by SEC EDGAR (Modules 4 & 5) |
| `DEFAULT_TAX_RESIDENCE` | Default tax profile (Module 1) |

---

## Graceful degradation

Every heavy dependency (QuantLib, alpaca-py, PyPortfolioOpt, spaCy, Celery,
pyvis…) is imported lazily. If one is missing the platform still boots and the
relevant page shows an actionable `pip install …` hint — you can run any subset
of modules.

---

## Legacy: Algohns V11 (Cloudflare Worker)

The V11 vanilla-JS application is preserved at `legacy/_worker_v11.js` (with the
`public/` assets and the `*.bat` / `*.ps1` helpers at the repo root). The active
`_worker.js` is now a thin **redirect Worker** that forwards `algohns.dreanquero.workers.dev`
to the live Streamlit app — see [`docs/DEPLOY.md`](docs/DEPLOY.md).

---

## Disclaimer

For research and paper-trading only. Tax rates are indicative and configurable;
verify against current legislation and your own fiscal advisor. Not investment
advice.
