# Algohns Quant Platform — Architettura

## Cos'è questo repository

Due metà, deliberatamente separate:

| Percorso | Cosa è | Runtime |
|---|---|---|
| `_worker.js`, `public/` | Algohns V11: Cloudflare Worker + frontend JS vanilla | Cloudflare edge |
| `platform/` | Piattaforma quant Python a 5 moduli | Python 3.11+ |

La separazione non è storica, è architetturale. I Workers non hanno un runtime
Python; la matematica pesante (grafi, ottimizzazione, QuantLib) non gira
sull'edge. Il confine è descritto in [Dove gira cosa](#dove-gira-cosa).

---

## Mappa delle repository analizzate

Hai fornito ~15 repository. Questa è la valutazione, incluse quelle **scartate**
e il perché — la parte più utile della risposta.

### Adottate come dipendenza diretta

| Repository | Modulo | Ruolo |
|---|---|---|
| `mementum/backtrader` *(opzionale)* | 3 | Event-driven backtesting quando serve simulare la microstruttura degli ordini. Il motore nativo copre il walk-forward su pesi di portafoglio, che è il caso d'uso reale qui. |
| `robertmartin8/PyPortfolioOpt` | 3 | Ottimizzazione. Usata come **cross-check**, non come motore: gli ottimizzatori sono nativi su SciPy per non ereditarne i pin di versione. |
| `pmorissette/ffn` *(opzionale)* | 3 | Metriche. Cross-check di Sharpe/Sortino/drawdown contro l'implementazione nativa. |
| `jadchaar/sec-edgar-downloader` *(opzionale)* | 4 | Download bulk dei filing per il crawl storico. |
| `alpacahq/alpaca-py` | 2 | SDK ufficiale Alpaca. |
| `celery/celery` | 2 | Task asincroni con retry e storico. |
| `lballabio/QuantLib` *(opzionale)* | 1 | Validazione della matematica obbligazionaria nativa. |
| `networkx` | 4 | Grafo diretto e centralità. |
| `spaCy` *(opzionale)* | 4 | NER per l'estrazione entità dai filing. |
| `pyvis` *(opzionale)* | 4 | Rendering interattivo del grafo. |

### Non adottate, e perché

| Repository | Verdetto | Motivo |
|---|---|---|
| `taosdata/TDengine`, `timescale/timescaledb` | **Scartate** | Time-series DB industriali. A questa scala (poche centinaia di titoli, storico giornaliero) i dati stanno in un DataFrame. Introdurre un DB a colonne ora aggiunge un servizio da gestire e zero capacità. Diventano sensate a dati tick-level o multi-utente. |
| `JerBouma/FinanceDatabase` | **Non necessaria** | Catalogo statico di strumenti. L'indice ufficiale SEC `company_tickers.json` copre il fabbisogno di risoluzione ticker→CIK con dati autoritativi e sempre aggiornati. |
| `JerBouma/FinanceToolkit` | **Sovrapposta** | Fa metriche + ratio + fundamentals: si accavalla con i moduli 3 e 5, portando il proprio livello dati. Meglio un confine netto che due strati che si contendono la stessa responsabilità. |
| `Librefolio/borsaItaliana-scraping` | **Rischiosa** | Scraping HTML di Borsa Italiana: fragile per costruzione (si rompe a ogni restyling) e di dubbia compatibilità con i ToS. Per i BTP la fonte corretta è MOT/Borsa via dati di mercato licenziati, o inserimento manuale del prezzo — che è ciò che fa il Modulo 1. |
| `hello245m/free-stockdb` | **Scartata** | Dataset non mantenuto, provenienza non documentata. Dati finanziari senza provenienza sono peggio di nessun dato. |
| `letianzj/QuantResearch`, `PythonForForex/Backtrader-for-backtesting` | **Riferimento** | Collezioni di notebook didattici, non librerie. Utili da leggere, non da importare. |
| `dppalomar/portfolioBacktest` | **Scartata** | È **R**, non Python. Ottima libreria, ma il ponte R↔Python non si giustifica: le sue funzioni sono replicate nativamente. |
| `kay-ou/SimTradeDesk` | **Non valutabile** | Repository non ispezionabile in questa sessione. Da rivalutare se indichi cosa ti serviva da qui. |
| `vectorbt` | **Rimandata** | Veloce e potente, ma la sua API vettorizzata spinge verso segnali su singolo asset; qui il problema è l'allocazione di portafoglio multi-asset con ribilanciamento periodico. Aggiungibile dietro un adapter se serve grid-search massiva. |
| `stefan-jockers/edgar-tools`, `pypland/financial-statements` | **Sostituite** | La **XBRL companyfacts API** di SEC (`data.sec.gov/api/xbrl/companyfacts/`) restituisce fatti già strutturati. Parsare l'HTML dei filing per estrarre bilanci quando esiste un endpoint strutturato è lavoro autoinflitto. |

### La decisione che conta

Il pattern ricorrente sopra: **le librerie che pesano poco e fanno una cosa
sono adottate; quelle che portano un proprio strato dati sono rifiutate.**
La matematica di base (YTM, duration, Sharpe, ottimizzazione, HRP) è
implementata nativamente su NumPy/SciPy — non per orgoglio, ma perché:

1. Le convenzioni di annualizzazione e day-count restano **esplicite e
   testate** invece di essere nascoste nei default di una dipendenza.
2. Niente dependency hell: `ffn`, `vectorbt` e `PyPortfolioOpt` pinnano
   versioni pandas incompatibili tra loro.
3. Ogni libreria opzionale serve da **cross-check**, che è più utile di un
   monopolio: due implementazioni che concordano valgono più di una che nessuno
   ha verificato.

---

## Struttura

```
platform/
├── app.py                      Orchestratore Streamlit
├── requirements.txt            Core: quello che la matematica richiede
├── requirements-optional.txt   Integrazioni; il codice degrada in modo esplicito
├── .env.example
├── core/
│   ├── config.py               Settings da ambiente; i segreti non hanno default
│   ├── cache.py                Cache su disco (scrittura atomica)
│   ├── daycount.py             ACT/ACT-ICMA, ACT/365F, ACT/360, 30/360
│   └── taxation.py             Motore fiscale IT/DE/FR/ES
├── modules/
│   ├── bond_engine.py          MODULO 1
│   ├── alpaca_execution.py     MODULO 2
│   ├── backtest_suite.py       MODULO 3 — walk-forward
│   ├── metrics.py              MODULO 3 — metriche
│   ├── optimizers.py           MODULO 3 — ottimizzatori
│   ├── market_data.py          Caricamento prezzi + fallback sintetico
│   ├── supply_chain_graph.py   MODULO 4
│   ├── sec_aggregator.py       MODULO 5
│   └── sec_client.py           Client EDGAR rate-limited condiviso
├── pages/                      5 pagine Streamlit
├── workers/
│   ├── celery_app.py           Celery + beat
│   ├── tasks.py                Job: sync, drift, rebalance, kill switch
│   └── scheduler.py            Fallback APScheduler in-process
└── tests/                      239 test
```

---

## Le decisioni non ovvie

### Modulo 1 — perché il YTM netto è un TIR, non uno sconto

Tassare le cedole al 12,5% e la plusvalenza a rimborso in un momento diverso
**non è esprimibile** come taglio sul rendimento lordo: i due flussi cadono in
punti diversi dell'asse temporale. Il motore costruisce i flussi post-imposta e
ne risolve il TIR.

Due convenzioni che spostano il risultato di basis point:

- **Asse temporale in periodi cedolari (ICMA)**, non in tempo calendario. I
  semestri reali durano 181-184 giorni: un asse ACT/365 prezza un titolo alla
  pari ~4 bp lontano dalla sua cedola. Con il conteggio in periodi il titolo
  alla pari rende **esattamente** la cedola (verificato a precisione macchina).
- **Capitalizzazione nominale alla frequenza cedolare**, non effettiva annua.
  Scontare `(1+y)^t` su un semestrale restituisce 4,04% per una cedola 4%:
  numero corretto, convenzione sbagliata.

E la distinzione TUIR che i calcolatori retail sbagliano:

| Componente | Categoria | Compensabile con minusvalenze? |
|---|---|---|
| Cedole | Redditi di capitale | **No** |
| Scarto di emissione | Redditi di capitale | **No** |
| Plusvalenza a rimborso | Redditi diversi | **Sì**, 4 anni |

Confonderle **sovrastima** il rendimento netto di chi ha uno zainetto fiscale.

### Modulo 2 — il lock paper è sull'hostname

`require_paper()` valida l'**hostname risolto**, non una sottostringa. Un URL
come `https://evil.example/paper-api.alpaca.markets` passerebbe un controllo
`"paper-api" in url`. Testato contro tre varianti di spoofing.

`execute_rebalance` richiede il token `EXECUTE`: un tick dello scheduler mal
configurato o un retry di Celery non possono operare da soli. Per lo stesso
motivo `task_acks_late=False` — un rebalance non è idempotente e non va
ri-consegnato in silenzio quando un worker muore a metà.

### Modulo 3 — il look-ahead bias è il rischio vero

A ogni ribilanciamento l'ottimizzatore vede solo `prices.loc[:data]`. Due test
lo **dimostrano**: perturbano l'ultimo 20% della storia (×3 su ogni prezzo) e
verificano che pesi e rendimenti precedenti siano bit-identici. Un motore che
stima sull'intero campione produce equity curve brillanti e perdite reali.

Vincolo correlato: la finestra minima. Senza `min_lookback` i primi
ribilanciamenti stimano la covarianza su una manciata di osservazioni e i pesi
sono rumore — che lusinga la curva proprio dove è meno affidabile. Il
portafoglio resta liquido fino alla prima data con storico sufficiente.

### Modulo 4 — tre scelte che decidono se è uno strumento o un quadro

1. **Il contagio segue gli archi, non la geografia.** Uno shock viaggia su
   relazioni di fornitura e di bilancio. Un arco Taiwan→Cupertino pesa più di
   un confine Italia→Slovenia. Il paese è attributo di **aggregazione**, mai
   topologia di propagazione.

2. **Archi osservati e inferiti restano distinguibili.** La disclosure è
   asimmetrica: ASC 280 obbliga a nominare i **clienti** sopra il 10% dei
   ricavi, i **fornitori** sono in gran parte volontari. Riempire i buchi con
   tavole input-output è legittimo; mescolarli senza etichetta trasforma una
   stima in un fatto. Ogni arco porta `provenance`, e un arco osservato batte
   sempre uno inferito — la provenienza non è un punteggio che una stima
   sicura possa superare.

3. **Due orologi, non uno.** Il repricing di mercato si muove in giorni; la
   rottura fisica brucia scorte per settimane prima di toccare la produzione.
   Il buffer di scorte produce la forma realistica:

   ```
   Shock su TSMC (blocco totale)
   giorno   0  TSM    ████████████  100%
   giorno  91  AMD    ███████        57%   ← 50gg di scorte esaurite
   giorno  98  NVDA   ████████       65%   ← 60gg di scorte esaurite
   giorno 196  DELL   ███            25%   ← terzo livello
   mai         MSFT   ·               0%   ← 120gg + alta sostituibilità
   ```

   Senza buffer si ottiene una cascata immediata, che è il segno di un modello
   che non ha pensato al tempo fisico.

### Modulo 5 — il problema è l'eterogeneità dei tag

Non il fetching. US-GAAP consente di esprimere lo stesso concetto con elementi
diversi: i ricavi compaiono come `Revenues`,
`RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet` o
`SalesRevenueGoodsNet` secondo il filer e l'anno. Leggere un tag solo
restituisce NaN per metà dell'S&P 500, e una tabella bucata sembra dato
mancante invece di un bug di mappatura.

Ogni voce dichiara una **catena di fallback** e ogni valore registra il tag
che l'ha prodotto, così un numero sorprendente si risale all'elemento.

Le durate sono filtrate per lunghezza: senza questo un dato Q4 taggato `FY`
vince lo slot annuale e sottostima l'esercizio del ~75%.

---

## Dove gira cosa

```
┌─────────────────┐   ┌──────────────────┐   ┌─────────────────┐
│  Python batch   │──>│ Cloudflare       │──>│  Browser        │
│                 │   │ Worker           │   │                 │
│ grafo, calibra  │   │ serve artefatti  │   │ render, tick    │
│ valida, ottim.  │   │ e API            │   │ interazione     │
└─────────────────┘   └──────────────────┘   └─────────────────┘
      pesante              stateless             interattivo
```

Streamlit è l'interfaccia per **analisi e ispezione**: ottima per tabelle,
parametri e grafici statici. Non è il posto per un loop di tick animato — si
ri-esegue top-to-bottom a ogni interazione e i componenti 3D vivono in iframe.

Per la simulazione temporale prevista come fase 2 (vedi sotto), il confine
corretto è: Python precalcola ed esporta (`SupplyChainGraph.to_json()` esiste
già per questo), il Worker serve, il browser renderizza e fa girare i tick.

---

## Sezione 6 — World Simulation

Simulazione stocastica forward-looking sul grafo reale, con un globo come
superficie di visualizzazione. Vive in due metà, come previsto:

| Percorso | Ruolo |
|---|---|
| `platform/modules/world_universe.py` | 54 aziende reali, coordinate della sede **operativa**, geografia dei ricavi, 59 archi di fornitura |
| `platform/modules/world_events.py` | 49 template di evento, arrivi Poisson per intensità annua |
| `platform/modules/world_forward.py` | Modello a fattori + fisica a due orologi + hazard di fallimento |
| `platform/modules/world_export.py` | Export compatto per il browser |
| `public/world/index.html` | Globo ortografico, motore replicato in JS, servito dal Worker |
| `platform/pages/6_World_Simulation.py` | Globo incorporato + analisi multi-seed in Python |

### La scelta che porta il peso: il mondo è indipendente dal portafoglio

Niente nel mondo simulato dipende da cosa detieni. Tre conseguenze:

1. il portafoglio si modifica **durante** la simulazione e il P&L si ricalcola
   subito, perché la valutazione è una riduzione su un mondo già calcolato;
2. due portafogli si confrontano sullo **stesso** percorso — l'unico confronto
   che isola il portafoglio invece della fortuna;
3. la matematica pesante precalcola in Python e il browser fa solo il replay,
   che è ciò che tiene il globo a frame rate.

### Due orologi, di nuovo — ma nel P&L

Gli shock macro sono **distribuiti** sulla durata dell'evento; quelli
societari sono **salti** del giorno. Un earnings miss gappa il titolo in una
seduta; il -17% di una recessione si materializza in trimestri. Applicare
anche i macro come salti giornalieri componeva moltiplicativamente in una
coda destra implausibile: una prima run dava il 90° percentile dei rendimenti
a 5 anni a **+209%**.

### Il catalogo non è neutrale rispetto al drift

I disastri sono più numerosi dei colpi di fortuna, quindi sommati sulle
intensità gli eventi portavano **-8,68%/anno** di rendimento atteso. Lasciato
non compensato, ogni simulazione diventava una spirale discendente: un test
forward-looking che non si può vincere non insegna nulla.

`expected_event_market_drift()` calcola quel contributo **dal catalogo** e il
simulatore lo sottrae, così `market_drift` è l'attesa incondizionata e la
correzione si ri-deriva da sola se domani aggiungi altri template.

### Calibrazione verificata, non dichiarata

Su 40-150 seed: volatilità realizzata **16,0%** contro il parametro 16%;
mediana a 5 anni **+32,8%** contro le **+32,1%** attese dal drift
*geometrico* (7% − 16%²/2). Confrontare con il drift *aritmetico* composto
(+40,3%) faceva sembrare rotta una calibrazione corretta — il benchmark era
sbagliato, non il modello. Fallimenti ~1,5x il tasso base, coerente con
large cap sotto stress.

### Limite dichiarato: due implementazioni

Il motore esiste in Python (riferimento, testato, 59 test) e in JavaScript
(anteprima interattiva nel browser). Due implementazioni dello stesso modello
divergono. Riconciliarle — idealmente facendo leggere al JS un percorso
*golden* prodotto da Python in CI — è lavoro da fare, e finché non esiste i
numeri nel browser vanno letti come indicativi.

---

## Fase 2 — validazione storica

Il simulatore esiste (Sezione 6). Quello che ancora manca è il
**cancello di credibilità**: un harness che
rigiochi shock storici noti (COVID feb-mar 2020, rate shock 2022, Tōhoku 2011,
carenza chip 2021) e confronti drawdown previsto e realizzato per settore. Un
motore di scenario che non riproduce uno shock noto sta animando un generatore
di numeri casuali.

Parametri di scenario in termini finanziari — sentiero dei tassi, prezzo
dell'energia, livello tariffario, chiusura di un chokepoint — **non** punti
esperienza stile RPG. Uno scenario deve essere discutibile e falsificabile.

Sul valore differenziale: Bloomberg PORT, MSCI BarraOne e Aladdin fanno già
stress fattoriale. Quello che fanno male è la **propagazione di secondo ordine
su grafo reale** e la leggibilità temporale. Il differenziatore sta lì, non nel
3D.

---

## Setup

```bash
cd platform
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # inserisci SEC_USER_AGENT con un contatto reale
streamlit run app.py
```

Il Modulo 1 non richiede credenziali né rete: è matematica pura. Il toggle
**Dati sintetici** rende utilizzabili i moduli 3 e 4 senza chiavi.

### Test

```bash
PYTHONPATH=platform python -m pytest platform/tests -q    # 301 test
```

Copertura: 43 bond engine · 35 Alpaca · 62 backtest/ottimizzatori · 54 supply
chain · 36 SEC · 59 world simulation · 12 smoke UI.

### Worker asincroni

```bash
celery -A workers.celery_app.celery_app worker --loglevel=info
celery -A workers.celery_app.celery_app beat   --loglevel=info
```

Senza Redis la piattaforma usa APScheduler in-process. I limiti sono reali: i
job muoiono col processo, non c'è storico di retry, e due processi Streamlit ne
eseguirebbero due copie. Celery quando questo conta.

---

## Limiti noti

Dichiarati perché contano più delle feature:

- **Qualità di estrazione (Modulo 4).** Regex e NER su prosa di bilancio
  producono falsi positivi. `min_confidence` filtra e ogni arco conserva la
  frase d'origine per audit, ma il grafo va **ispezionato**, non fidato.
- **Copertura fornitori.** Struttturalmente rada per ragioni di disclosure.
  `coverage()` riporta la quota di archi osservati: va mostrata accanto a ogni
  risultato.
- **Profili fiscali semplificati.** Aliquote statutarie per pianificazione, non
  consulenza fiscale. La Spagna è progressiva (19-30%) e qui è modellata al
  21%; la Kirchensteuer tedesca non è modellata.
- **I dati sintetici non sono dati di mercato.** Riproducibili e correlati,
  utili per validare la meccanica. Etichettati in ogni pagina che li usa.
- **Nessuna validazione storica del modulo 4.** La propagazione è
  internamente coerente e testata, ma non ancora confrontata con episodi
  reali. È il lavoro di fase 2, e finché non è fatto i numeri di secondo
  ordine sono indicativi.
- **Il globo non è una mappa.** Le coste sono poligoni volutamente a bassa
  risoluzione: orientano lo sguardo senza millantare una precisione
  cartografica che la simulazione non ha.
