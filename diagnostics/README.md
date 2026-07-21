# Phase 0 diagnostics — reproducible

Every number in [`../DIAGNOSI.md`](../DIAGNOSI.md) is produced by one of these
scripts. Run them from the repository root.

## Requirements

- Node.js 18+ (measured on v22.22.2)
- Python 3.11+ with `scikit-learn`, `pandas`, `numpy` (`pip install scikit-learn pandas numpy`)

## Scripts

| Script | Phase 0 item | Needs raw match data? |
|---|---|---|
| `diag.js` | 0.1 model size / parse time, 0.2 name hit-rate, 0.5 worker-vs-export divergence | no — uses shipped `model.json` |
| `parity_check.py` | 0.5 sklearn `predict_proba` == JS `sigmoid(z)` | no — reads `diag.js` output |
| `mechanism_03.py` | 0.3 which model the `metrics` block describes | no — synthetic data |
| `ranking_baseline.py` | 0.4 "better-ranked player wins" baseline | **yes** — Jeff Sackmann `tennis_atp` |

## Run

```bash
# 0.1 / 0.2 / 0.5 (writes diagnostics/_artifacts_parity_rows.json)
node diagnostics/diag.js

# 0.5 confirmation (reads the file diag.js wrote)
python3 diagnostics/parity_check.py

# 0.3 mechanism (self-contained synthetic run)
python3 diagnostics/mechanism_03.py

# 0.4 — only where GitHub / the ATP data is reachable
cd baseline-v22/training
python3 ../../diagnostics/ranking_baseline.py --start-year 2015 --end-year 2025
```

## Why 0.3 (empirical) and 0.4 are "non verificato" here

This session's egress policy denies (HTTP 403) every external data host —
`github.com`, `raw.githubusercontent.com`, `tennis-data.co.uk`,
`tennisabstract.com`, HuggingFace, and even live `api.api-tennis.com`. Only the
pypi and npm registries are allowlisted. The Jeff Sackmann `tennis_atp` CSVs
that `train_model.py` needs to rebuild the labelled test set are therefore
unreachable, so the ranking baseline (0.4) and the *empirical* "which candidate
won" half of 0.3 cannot be measured **in this environment**. The scripts are
written and verified against the code path; run them where the data is
reachable to fill in the two numbers.
