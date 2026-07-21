# TennisForest GreenCode v22 — Live Tracking & Model Diagnostic

Questa versione mette al centro il registro delle previsioni reali.

## Cosa caricare su Cloudflare
Carica solo la cartella:

```txt
UPLOAD_THIS_TO_CLOUDFLARE
```

## Variabili Cloudflare consigliate

```txt
MODEL_VERSION=greencode-realtime-worker-v22
EDGE_MIN=0.05
CONF_MIN=0.60
MIN_ODDS=1.25
MAX_ODDS=3.25
MAX_OVERROUND=1.10
ENABLE_ODDS=true
REAL_ONLY=true
DEMO_FALLBACK=false
FUTURE_DAYS=60
BACKTEST_DAYS=20
MAX_ODDS_MATCH_CALLS=60
SAVE_PREDICTIONS=true
FIREBASE_PROJECT_ID=tennisforest-8456e
FIREBASE_API_KEY=<la tua Firebase web API key>
```

## Endpoint principali

```txt
/api/health
/api/model
/api/future
/api/predictions
/api/stats
/api/settle
/api/tracking-check?debug=1
```

## Tracking

- Ogni previsione futura viene salvata una sola volta in `predictions`.
- I refresh successivi non modificano la previsione originale.
- I refresh salvano solo `latestSnapshot` e un record in `predictionRuns`.
- Il settlement aggiorna solo `actualWinner`, `correct`, `profit100`, `settledAt` quando il match è davvero finito.

## Nota Firebase

Se `/api/tracking-check?debug=1` fallisce, il Worker non può scrivere su Firestore. Controlla:

- `FIREBASE_PROJECT_ID`;
- `FIREBASE_API_KEY`;
- Firestore rules;
- eventuali restrizioni sulla API key.
