# Quick Start Alpaca Paper — Algohns V11

## Secrets richiesti

Servono le chiavi Paper Trading di Alpaca:

```txt
ALPACA_API_KEY
ALPACA_SECRET_KEY
```

Il base URL deve rimanere:

```txt
https://paper-api.alpaca.markets
```

Algohns V11 blocca l'esecuzione se il Worker viene configurato con un endpoint non paper.

## Test manuale dopo deploy

Nel sito:

1. Settings / Connections -> Test connections
2. Universe Explorer -> Load universe
3. Control Center -> Run Strategy Engine
4. Backtest Lab -> Load Backtest Data
5. Backtest Lab -> Run Backtest
6. Orders & Control -> controlla order preview
7. Orders & Control -> invia paper orders solo se vuoi testarli
8. Kill Switch -> verifica che fermi tutto e cancelli gli open paper orders

## Endpoint utili

```txt
/api/health
/api/broker/alpaca/status
/api/broker/alpaca/account
/api/broker/alpaca/positions
/api/broker/alpaca/orders
/api/broker/alpaca/assets
/api/broker/alpaca/clock
/api/market/bars
/api/market/snapshots
/api/market/news
```

## Note sul backtest

Il backtest richiede sempre:

```txt
Load Backtest Data
```

prima di:

```txt
Run Backtest
```

Se cambi strategia, universo, periodo o benchmark, il dataset diventa stale e devi ricaricare i dati.

I fondamentali sono disabilitati nei backtest finché non viene aggiunto un provider point-in-time. Questa scelta evita look-ahead bias.
