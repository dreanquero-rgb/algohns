#!/usr/bin/env python3
"""Phase 0.5 — confirm the worker's raw sigmoid replicates sklearn predict_proba.

Loads 200 feature rows emitted by diag.js (built from real player pairs),
reconstructs an sklearn LogisticRegression whose coef_/intercept_ are the raw
coefficients exported in model.json, and compares its predict_proba to:
  (a) the JS pure sigmoid(z)  -> must be ~0 (faithful export math)
  (b) the JS worker shipped probability -> the real calibration divergence
"""
import json, os, numpy as np
from sklearn.linear_model import LogisticRegression

HERE = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(HERE, "_artifacts_parity_rows.json")))
X = np.array([r["features"] for r in d["rows"]], dtype=float)
coef = np.array(d["coefficients"], dtype=float)
intercept = float(d["intercept"])
js_pure = np.array([r["pure_sigmoid"] for r in d["rows"]], dtype=float)
js_worker = np.array([r["worker_prob"] for r in d["rows"]], dtype=float)

# Reconstruct the exact exported logistic in sklearn (raw feature space).
clf = LogisticRegression()
clf.classes_ = np.array([0, 1])
clf.coef_ = coef.reshape(1, -1)
clf.intercept_ = np.array([intercept])
sk = clf.predict_proba(X)[:, 1]

a = np.abs(sk - js_pure)
b = np.abs(sk - js_worker)
print(json.dumps({
    "rows": len(X),
    "sklearn_vs_js_pure_sigmoid_max": float(a.max()),
    "sklearn_vs_js_pure_sigmoid_median": float(np.median(a)),
    "sklearn_vs_worker_shipped_max": round(float(b.max()), 6),
    "sklearn_vs_worker_shipped_median": round(float(np.median(b)), 6),
}, indent=2))
