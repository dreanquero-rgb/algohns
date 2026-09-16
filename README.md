# Algohns V11 — Alpaca Paper Quant Asset Manager OS

Algohns V11 is a clean, professional, Alpaca Paper-only base for a Quant Asset Manager OS. It is built as a Cloudflare Worker plus a static vanilla JavaScript frontend. No React, no Vue, no complex build step.

## What this version does

- Control Center with sticky Play / Pause / Kill Switch
- Alpaca Paper connection layer
- Universe Explorer from Alpaca active tradable assets
- Transparent included/excluded asset reasons
- Strategy Engine with Defensive, Balanced, Advanced and Aggressive profiles
- Regime Engine with voting details
- Portfolio cockpit with allocation treemap and position actions
- Orders & Control with paper order preview and execution journal
- Backtest Lab with mandatory Load Backtest Data before Run Backtest
- Risk Center with drawdown, VaR, CVaR, volatility, beta proxy, concentration and stress tests
- News Intelligence using Alpaca news when available
- Export snapshot HTML, trade log CSV, strategy JSON and backtest report
- Real-money execution locked in both UI and worker

## Required secrets

Cloudflare Worker secrets:

```txt
ALPACA_API_KEY
ALPACA_SECRET_KEY
```

The project also sets:

```txt
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_BASE_URL=https://data.alpaca.markets
```

The worker refuses non-paper Alpaca execution.

## Deploy target

Worker name:

```txt
algohns
```

Expected URL:

```txt
https://algohns.dreanquero.workers.dev
```

## One-click Windows flow

1. Extract the zip into a fresh folder.
2. Double-click `START_HERE.bat`.
3. Insert Alpaca Paper keys when asked, or keep existing saved keys.
4. Open the deployed URL.
5. Press `CTRL + F5`.
6. Go to Settings / Connections and run Live build check.

## Files

```txt
_worker.js
package.json
wrangler.toml
public/index.html
public/assets/app.js
public/assets/styles.css
public/assets/algohns-mark.svg
START_HERE.bat
DEPLOY.bat
CONFIGURA_CHIAVI.bat
SETUP_ALGOHNS.ps1
QUICK_START_AUTOMATICO.md
QUICK_START_ALPACA.md
```

## QA performed before packaging

```txt
node --check _worker.js
node --check public/assets/app.js
```

Also checked: clean V11 branding, Alpaca Paper-only UI, stable navigation and real-money lock.

---

## Piattaforma quant Python (`platform/`)

Oltre al Worker Cloudflare descritto sopra, il repository contiene una
piattaforma Python a 5 moduli sotto `platform/`:

| Modulo | Cosa fa |
|---|---|
| 1 — Bond Engine | YTM lordo/netto per BTP, Bund, OAT, Bonos ed Eurobond; duration modificata, convexity; fiscalità IT/DE/FR/ES con distinzione TUIR fra redditi di capitale e redditi diversi |
| 2 — Alpaca Engine | Esecuzione ordini e ribilanciamento asincrono, con lock paper applicato sull'hostname risolto |
| 3 — Backtest Suite | Walk-forward strettamente causale; Max Sharpe, Min Variance, Risk Parity, HRP, Black-Litterman |
| 4 — Supply Chain | Grafo S&P 500 da filing SEC e propagazione shock a due orologi (repricing veloce, rottura fisica lenta) |
| 5 — SEC Aggregator | XBRL companyfacts normalizzato con catene di fallback sui tag; confronto multi-ticker |

### Avvio rapido

```bash
cd platform
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # SEC_USER_AGENT richiede un contatto reale
streamlit run app.py
```

Il Modulo 1 funziona senza credenziali né rete. Il toggle **Dati sintetici**
rende utilizzabili i moduli 3 e 4 senza chiavi API.

### Test

```bash
PYTHONPATH=platform python -m pytest platform/tests -q    # 239 test
```

Architettura, valutazione delle repository esterne e limiti noti:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

**Denaro reale bloccato** in entrambe le metà: il Worker rifiuta l'esecuzione
non-paper, e il client Python solleva `LiveTradingBlocked` per qualunque host
diverso da `paper-api.alpaca.markets`.
