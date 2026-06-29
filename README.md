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
- Autonomous Strategy Engine runs on a Cloudflare Cron Trigger, on the edge, with or without the site open

## Always-on autonomous trading

The Strategy Engine now runs server-side on a Cloudflare Cron Trigger (every 5 minutes by default, see `[triggers]` in `wrangler.toml`), not just when you click "Run Strategy Engine" in the browser. State (Play/Pause/Kill, strategy, exclusions) and the order journal are persisted in a Cloudflare KV namespace so the engine keeps acting on your behalf even if no browser tab is open.

Setup:

```bash
wrangler kv namespace create algohns-engine-state
```

Copy the printed namespace `id` into the `[[kv_namespaces]]` block in `wrangler.toml` (replace `REPLACE_WITH_KV_NAMESPACE_ID`), then deploy. Press Play in Control Center once to arm the autonomous engine; Pause/Kill stop it the same way, from any device.

The worker only submits paper orders autonomously when: engine state is "running" (Play pressed), the Kill Switch is not active, and the Alpaca Paper market clock reports the market open.

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
