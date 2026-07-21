# DIAGNOSI.md — TennisForest v23, Fase 0 (baseline misurata)

Numeri **ottenuti eseguendo codice** contro gli artefatti reali della v22
(`baseline-v22/UPLOAD_THIS_TO_CLOUDFLARE/`). Dove un numero non è misurabile in
questo ambiente lo dichiaro **`non verificato`** con la ragione — non è inventato.

- Ambiente di misura: Node `v22.22.2`, Python `3.11.15`, scikit-learn `1.9.0`,
  pandas/numpy correnti.
- Riproduzione: vedi [`diagnostics/README.md`](diagnostics/README.md). Ogni cifra
  qui sotto è l'output di uno script committato.

> **Vincolo ambientale che condiziona la Fase 0 (e le Fasi 1–2).**
> La policy di rete di questa sessione **nega (HTTP 403) tutti** gli host dati
> esterni: `github.com`, `raw.githubusercontent.com`, `tennis-data.co.uk`,
> `tennisabstract.com`, HuggingFace e persino `api.api-tennis.com`. Solo i
> registry pypi/npm sono permessi (verificato: `curl` → 403 su ognuno, e il
> proxy registra `connect_rejected … policy denial`). Conseguenza diretta: i
> numeri che richiedono di **ricostruire il test set etichettato** dai dati
> Jeff Sackmann (0.4, e la metà *empirica* di 0.3) non sono misurabili **qui**.
> Gli script per calcolarli sono scritti, committati e verificati sul percorso
> di codice: vanno eseguiti dove i dati sono raggiungibili.

---

## Riepilogo (i 5 numeri)

| # | Domanda | Risultato misurato |
|---|---|---|
| **0.1** | Peso e parse di `model.json` | **2.821 MiB** (2 958 273 byte); `JSON.parse` in Node **~16 ms** (mediana di 7; 24 ms il primo). **1 730** giocatori × 21 campi; **30 877** coppie h2h; 19 chiavi top-level. |
| **0.2** | Hit-rate nomi API-Tennis via `playerKey()` | **0 % (0/50)** sul formato reale `"Cognome I."`. Controllo: se i nomi arrivassero come `"Nome Cognome"` → **100 %**. Il resolver non ha alcuna conversione `"Cognome I." → "Nome Cognome"`. |
| **0.3** | Le metriche in `model.json` sono del modello esportato o del "best"? | **Del "best"** (argmin log-loss tra Logistic/RF/GB), **non** necessariamente dell'export. Dimostrato per esecuzione: quando vince GB, il blocco `metrics` riporta 0.8658 acc mentre la logistica esportata vale in proprio 0.5558 acc. Quale caso valga per l'artefatto *spedito* → **`non verificato`** (dati bloccati), ma i valori spediti sono coerenti con una logistica. |
| **0.4** | Baseline "vince il ranking migliore" sullo stesso test set | **`non verificato`** — richiede il dataset Jeff Sackmann, bloccato (403) in questo ambiente. Script pronto: `diagnostics/ranking_baseline.py`. |
| **0.5** | Parità export: `predict_proba` sklearn vs sigmoid del worker (200 righe) | Matematica dell'export **fedele**: `sklearn` vs `sigmoid(z)` JS → **max 2.2e-16**, mediana 0. Ma la probabilità **realmente servita** dal worker diverge da sklearn di **max 0.187, mediana 0.036** per via di `evidenceShrink`+`temperature`+`priorBlend`+`clamp`. |

---

## 0.1 — `model.json`: peso, chiavi, tempo di parse

Script: `diagnostics/diag.js` (blocco `0.1`). Misurato in Node.

```
size            : 2 958 273 byte = 2.821 MiB (2.958 MB)
JSON.parse      : 23.6 ms (prima esecuzione)  |  15.8 ms (mediana di 7)
players         : 1 730 chiavi, 21 campi ciascuna
h2h             : 30 877 coppie
top-level keys  : 19  (version, created_at, data_source, data_sources,
                  intelligence, start_year, end_year, trained_through,
                  training_rows, test_rows, features, intercept, coefficients,
                  metrics, calibration, confidence_buckets, top_features,
                  players, h2h)
```

Chiavi in `players`: le 21 statistiche per giocatore (`elo`, `surface_elo`,
`matches`, `wins`, `losses`, `win_rate`, `surface_win_rate`, `form_5`,
`form_10`, `rank`, `rank_points`, `ranking_momentum`, `serve_strength`,
`return_strength`, `hold_rate`, `break_rate`, `ace_rate`, `double_fault_rate`,
`tiebreak_win_rate`, `vs_top50_win_rate`, `surface_form_10`).

Chiavi in `h2h`: 30 877 stringhe `"<key_a>__<key_b>"` → conteggio vittorie.

**Implicazione per la Fase 2.** Il worker fa `JSON.parse` dell'intero blob per
ogni richiesta a freddo: ~2.9 MB e ~16 ms di CPU solo di parse, più il fetch
dell'asset. È esattamente il monolite che il requisito "nessun percorso parsa
> 100 KB" impone di eliminare con lo shard per iniziale + `h2h.json`.

## 0.2 — Hit-rate del resolver nomi

Script: `diagnostics/diag.js` (blocco `0.2`).

**Metodo.** Le chiavi di `model.players` sono `"nome cognome"` normalizzato
(da `norm_name` in `train_model.py`). API-Tennis invia invece `"Cognome I."`
(`"Djokovic N."`, `"Auger-Aliassime F."`, `"Davidovich Fokina A."`). Prendo 50
giocatori reali del modello, li riformatto nel formato API-Tennis `"Cognome I."`
(cognome = tutti i token dopo il primo; primo token → iniziale) e li risolvo con
la **`playerKey()` identica** a quella del worker, verificando l'appartenenza a
`model.players`.

```
campione                         : 50
hit via playerKey("Cognome I.")  : 0  →  0.0 %
controllo "Nome Cognome"         : 50 →  100.0 %
```

Esempi (tutti miss):

```
model key "illya marchenko"       →  API "Marchenko I."       →  key "marchenko i"    →  MISS
model key "sergiy stakhovsky"     →  API "Stakhovsky S."      →  key "stakhovsky s"   →  MISS
model key "christopher eubanks"   →  API "Eubanks C."         →  key "eubanks c"      →  MISS
```

**Interpretazione.** `playerKey("Djokovic N.")` = `"djokovic n"`, che non
coincide mai con la chiave del modello `"novak djokovic"`. Non esiste nel worker
alcuna tabella di alias né conversione bidirezionale `"Cognome I." ↔ "Nome
Cognome"`. Nel formato che l'API restituisce davvero **il modello offline non
risolve quasi nessun giocatore**, e `buildOfflineFeatureVector` cade sui default
(Elo 1500, win_rate 0.5, ecc.): il "cervello storico" di fatto non viene usato.
Questo è il difetto più grave della v22 ed è la ragione del target Fase 2
`name-coverage > 95 %` con endpoint dedicato.

*(Nota onesta sul metodo: senza chiave API-Tennis e con l'host bloccato non
posso pescare 50 nomi da una risposta live; li derivo dai giocatori reali del
modello riformattandoli nel formato documentato dell'API. È una stima fedele del
formato, non un campione live.)*

## 0.3 — Le metriche appartengono all'export o al "best"?

Script: `diagnostics/mechanism_03.py`. **Verificato per esecuzione**, non per
lettura.

`train_model.py` sceglie `best = argmin log-loss` tra Logistic/RF/GB e poi
`export_model` scrive `metrics = {…best…}`. Ma `export_model` **esporta sempre
una logistica**: se `best` non ha lo step `logisticregression`, ne allena una
nuova (per giunta su *tutte* le righe, non solo il train) e ne esporta i
coefficienti — **tenendo però le metriche di `best`**.

Riproduzione con segnale non-lineare (XOR) così che vinca un albero:

```
Logistic Regression : acc=0.5108  logloss=0.6929  auc=0.5162
Random Forest       : acc=0.7767  logloss=0.6426  auc=0.8505
Gradient Boosting   : acc=0.8658  logloss=0.5654  auc=0.8937   ← BEST

blocco "metrics" nel JSON esportato   : acc=0.8658  auc=0.8937   (= GB)
metriche PROPRIE della logistica esp. : acc=0.5558  auc=0.5638
json_metrics_describe_exported_logistic: FALSE
```

**Conclusione.** Le metriche in `model.json` sono quelle del *best*, e
descrivono l'export **solo se** il best è la logistica. Nell'artefatto spedito i
coefficienti sono 29 pesi logistici con `intercept ≈ 8.9e-19` e `version`
`"greencode-logistic-…"`, e i valori (acc **0.646325**, log-loss 0.619429,
brier 0.215862, roc_auc **0.710837**) sono coerenti con una logistica; **quale
candidato abbia effettivamente vinto sul run reale 2015–2025 è `non
verificato`** perché ri-eseguire il training richiede i dati Jeff Sackmann,
bloccati (403) qui. La discrepanza è possibile per costruzione e va chiusa in
Fase 1.5 con un test di parità in CI.

## 0.4 — Baseline "vince il ranking migliore"

Script pronto: `diagnostics/ranking_baseline.py` (riusa `load_data` +
`build_dataset` del trainer → split e feature identici; baseline =
`sign(rank_log_diff)` sul test set, con anche il baseline Elo in omaggio).

**Risultato: `non verificato` in questo ambiente.** Serve il dataset Jeff
Sackmann `tennis_atp` (o un `canonical_matches.csv` da `build_data_lake.py`) e
ogni host è bloccato (403). Eseguire dove i dati sono raggiungibili:

```bash
cd baseline-v22/training
python3 ../../diagnostics/ranking_baseline.py --start-year 2015 --end-year 2025
```

Riferimento atteso dalla letteratura ATP: il favorito di ranking vince circa il
**63–65 %** dei match del tour. Con l'accuracy del modello a **0.6463**, il
confronto con questo baseline è *il* numero che decide se il modello ha valore —
ed è per questo obbligatorio prima di procedere alla Fase 1.

## 0.5 — Parità export: sklearn vs sigmoid del worker

Script: `diagnostics/diag.js` (genera 200 righe da coppie di giocatori reali) +
`diagnostics/parity_check.py` (ricostruisce la logistica esportata in sklearn e
confronta).

```
sklearn predict_proba  vs  sigmoid(z) JS   :  max 2.22e-16   mediana 0.0
sklearn predict_proba  vs  prob. SERVITA   :  max 0.186619   mediana 0.036346
```

**Due verità distinte.**
1. La matematica dell'export è **fedele**: `z = intercept + Σ coef·feature`, e la
   `sigmoid(z)` del worker riproduce `predict_proba` di sklearn a livello di
   epsilon macchina (2.2e-16). I coefficienti raw esportati sono corretti.
2. Ma il worker **non serve** quella probabilità. `scoreOfflineModel` applica
   in sequenza `evidenceShrink = 0.5 + 0.5·evidence`, `temperature = 1.1`,
   `priorBlend` (0.05/0.12/0.24 secondo `dataQuality`) e `clamp[0.08, 0.92]`.
   Il risultato diverge dal modello esportato di **fino a 0.187** (mediana
   0.036) di probabilità.

**Implicazione.** Le metriche pubblicate (0.646 acc, ecc.) sono della logistica
*grezza*; la probabilità che alimenta predizioni e value-bet è un'altra
funzione. Il target Fase 1.5 (`differenza max < 1e-6` tra sklearn e JS) è
raggiungibile **solo** decidendo esplicitamente cosa è "il modello": o si esporta
la calibrazione dentro `model-core.json` e sklearn la applica pure (parità
vera), oppure la si toglie dal worker. Oggi le due cose non coincidono.

---

## Cosa serve per sbloccare i numeri mancanti

`0.4` e la metà empirica di `0.3` sono a un comando di distanza **appena i dati
sono raggiungibili**. Due strade:

1. Eseguire `diagnostics/ranking_baseline.py` e `diagnostics/mechanism_03.py`
   (con dati reali al posto del sintetico) in locale o in un ambiente la cui
   network policy consenta `github.com`.
2. Oppure allentare la policy di egress di questa sessione per i soli host dati
   (Jeff Sackmann su GitHub) e li rieseguo qui.

**STOP richiesto dalla Fase 0.** Questi sono i 5 numeri. Attendo conferma prima
di procedere alla Fase 1 — e in particolare una decisione sull'accesso ai dati,
perché senza di esso Fase 1 (ri-training walk-forward) e Fase 2 (layer live
API-Tennis) non sono eseguibili end-to-end in questo ambiente.
