# Training v22 — Multi-Source Tennis Data Lake

## Pipeline consigliata

```bash
cd training
pip install -r requirements.txt
python build_data_lake.py --start-year 2010 --end-year 2025 --include-mcp
python train_model.py --start-year 2010 --end-year 2025 --export ../UPLOAD_THIS_TO_CLOUDFLARE/model.json
```

## Fonti automatiche / predisposte

- Jeff Sackmann `tennis_atp`: risultati, ranking, stats match-level.
- Tennis-Data.co.uk: odds storiche, best-effort downloader + supporto file locali.
- Match Charting Project: point-by-point / charting, opzionale e non-commerciale.
- `training/data/odds/`: CSV/XLSX odds caricati da te.
- `training/data/enrichments/`: CSV/XLSX da altri provider/API/export legali.

## Anti-overlap

`build_data_lake.py` crea:

```txt
training/data/canonical_matches.csv
training/data/data_lineage.json
```

Ogni match ha un `match_key`. Le fonti esterne arricchiscono il match esistente e non generano duplicati.

## Output

`train_model.py` esporta:

```txt
UPLOAD_THIS_TO_CLOUDFLARE/model.json
```

con:

- metadata sorgenti;
- feature usate;
- metriche temporali;
- calibration;
- player profiles;
- H2H;
- data depth compatibility.
