# Quick Start Automatico — Algohns V11

## Prima installazione

1. Estrai lo zip in una cartella nuova.
2. Fai doppio clic su:

```txt
START_HERE.bat
```

Lo script:

- controlla Node.js
- installa o aggiorna Wrangler 4 nel progetto
- controlla se ci sono già chiavi Alpaca salvate localmente
- controlla se ci sono già secrets Cloudflare
- ti permette di mantenerle o sostituirle
- esegue il deploy sul Worker `algohns`

## Aggiornamenti successivi

Dopo la prima configurazione, usa:

```txt
DEPLOY.bat
```

Questo fa deploy senza chiederti di reinserire le chiavi.

## Cambiare le chiavi Alpaca

Usa:

```txt
CONFIGURA_CHIAVI.bat
```

Le chiavi vengono salvate anche in:

```txt
%USERPROFILE%\.algohns\alpaca.env
```

Così le versioni successive possono riusarle.

## Dopo il deploy

Apri:

```txt
https://algohns.dreanquero.workers.dev
```

Poi fai:

```txt
CTRL + F5
```

Infine vai in:

```txt
Settings / Connections -> Live build check
```

Build attesa:

```txt
algohns-v11-alpaca-core-2026-06-14
```
