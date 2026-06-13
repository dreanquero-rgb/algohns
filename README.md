# Algohns V9.2 · Fix & Polish

Versione V9.2 con interfaccia broker-isolata e hotfix su chart, orders, eToro diagnostics e azioni posizione.

## Cosa cambia in V9.2

- Sostituito il singolo `window._equityChartInstance` / `_pieChartInstance` con `window._chartRegistry` per evitare conflitti Chart.js tra Control Center, Portfolio Combined, Alpaca ed eToro.
- `setChartRange()` ora aggiorna il chart corretto per broker tramite ID canvas.
- `afterRenderPortfolio()` ha retry loop se Chart.js non è ancora caricato.
- Tooltip Chart.js con tema stabile dark/light, senza dipendere da CSS var lette troppo presto.
- Date asse X formattate in modo leggibile per backtest e portfolio chart.
- Bottoni Exit / Reduce con feedback visivo sulla riga durante l'operazione.
- Reduce 50% corretto: non dimezza due volte quantità/unità.
- Orders page ripulita: un solo bottone primary, kill switch isolato a destra.
- Broker bar con Run engine / Send broker / Sync nelle viste Alpaca ed eToro.
- eToro sync più robusto: legge posizioni anche da strutture nested del portfolio/P&L endpoint.
- eToro asset cache alimentata anche dalle posizioni reali, così positionId/instrumentId non vengono persi.
- Order diagnostics: se eToro non prepara ordini perché mancano instrumentId, ora il motivo appare in pagina invece di sembrare un bug silenzioso.
- Worker eToro: portfolio/orders più robusti su `/trading/info/demo/pnl`, `/portfolio`, `/portfolio-details`, `/account`.

## Nota eToro

Algohns non inventa instrumentId eToro. Per inviare ordini demo eToro, l'asset deve avere un `instrumentId` verificato tramite portfolio sync o Universe Explorer/Search eToro. Se manca, la pagina Orders mostra una diagnostica esplicita.

## Sicurezza

- Alpaca: solo Paper Trading API.
- eToro: solo Demo execution endpoints.
- Real-money trading resta bloccato lato UI e worker.

## File principali

- `public/assets/app.js` — SPA frontend, rendering, portfolio, orders, diagnostics.
- `public/assets/styles.css` — design system V9 flat.
- `_worker.js` — Cloudflare Worker API adapter.
- `public/index.html` — shell SPA.
