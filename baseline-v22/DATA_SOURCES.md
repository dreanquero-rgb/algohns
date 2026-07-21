# TennisForest GreenCode v22 — Multi-Source Data Lake

## Principio corretto

TennisForest non deve usare l'API live come cervello predittivo.

- **API-Tennis**: calendario reale, quote realtime, risultati, settlement.
- **model.json**: intelligenza storica allenata offline.
- **Firestore**: registro immutabile delle previsioni live e dei risultati reali.

## Fonte master vs fonti enrichment

Per evitare overlap, v22 usa una struttura da data warehouse:

1. **Backbone dei risultati**
   - Fonte primaria: Jeff Sackmann `tennis_atp`.
   - Contiene risultati storici, ranking alla data, superficie, torneo, round, score e statistiche match-level quando disponibili.

2. **Odds storiche**
   - Fonte: Tennis-Data.co.uk o CSV/XLSX locali equivalenti.
   - Non crea nuove partite duplicate: arricchisce il match già esistente tramite `match_key`.

3. **Point-by-point / charting**
   - Fonte opzionale: Jeff Sackmann Match Charting Project.
   - Licenza non-commerciale: usarlo solo se compatibile con il progetto o con permesso.
   - Arricchisce solo i match coperti dal progetto.

4. **Export/API legali aggiuntivi**
   - Ultimate Tennis Statistics, Tennis Abstract, Kaggle, feed pagati, API-Tennis export, ecc.
   - Il sistema accetta CSV/XLSX in `training/data/enrichments/` se l'utente ha diritto a usarli.

## Deduplica

Ogni match riceve un `match_key` unico:

```txt
date | tournament | round | sorted(player1, player2)
```

Poi viene hashato. Se due fonti parlano dello stesso match, non si creano due righe. Si fa merge dei campi:

```txt
Jeff result + Tennis-Data odds + MCP stats + local enrichment
```

## Comandi

```bash
cd training
pip install -r requirements.txt
python build_data_lake.py --start-year 2010 --end-year 2025 --include-mcp
python train_model.py --start-year 2010 --end-year 2025 --export ../UPLOAD_THIS_TO_CLOUDFLARE/model.json
```

Se vuoi usare odds locali:

```txt
training/data/odds/*.csv
training/data/odds/*.xlsx
```

Se vuoi usare altri export legali:

```txt
training/data/enrichments/*.csv
training/data/enrichments/*.xlsx
```

## Nota onesta

La v22 include la pipeline per raccogliere più dati reali senza overlap. Il `model.json` incluso resta compatibile con v22, ma non diventa magicamente premium finché non viene rilanciato il training con i dataset scaricati o caricati localmente.
