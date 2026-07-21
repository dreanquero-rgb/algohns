#!/usr/bin/env python3
"""Phase 0.3 (mechanism) — verify by EXECUTION whether model.json 'metrics'
describe the exported logistic or the best-of {Logistic, RF, GB}.

We reproduce train_model.py's real code path (evaluate_model -> pick best by
log loss -> export_model) on a synthetic dataset built with a nonlinear
(interaction) signal so a tree model wins. Then we reload the exported JSON,
recompute the EXPORTED logistic's own metrics from its raw coefficients, and
compare to the JSON 'metrics' block.
"""
import sys, json, math
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str((HERE / ".." / "baseline-v22" / "training").resolve()))
import train_model as tm
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score, brier_score_loss

rng = np.random.default_rng(0)
N = 6000
F = tm.FEATURES
X = rng.normal(0, 1, size=(N, len(F)))
df = pd.DataFrame(X, columns=F)
# Nonlinear XOR-style signal on two features -> logistic cannot separate it,
# gradient boosting / random forest can.
a = df["elo_diff"].values
b = df["surface_elo_diff"].values
logit = 2.5 * np.sign(a) * np.sign(b) + 0.3 * df["win_rate_diff"].values
p = 1 / (1 + np.exp(-logit))
df["y"] = (rng.uniform(size=N) < p).astype(int)
df["date"] = pd.date_range("2015-01-01", periods=N, freq="D")

cut = int(N * 0.8)
train, test = df.iloc[:cut], df.iloc[cut:]
Xtr, ytr = train[F], train["y"].astype(int)
Xte, yte = test[F], test["y"].astype(int)

from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier

candidates = [
    ("Logistic Regression", Pipeline([("imputer", SimpleImputer(strategy="median")), ("standardscaler", StandardScaler()), ("logisticregression", LogisticRegression(max_iter=3000, C=0.8))])),
    ("Random Forest", Pipeline([("imputer", SimpleImputer(strategy="median")), ("randomforestclassifier", RandomForestClassifier(n_estimators=200, min_samples_leaf=25, random_state=42, n_jobs=-1))])),
    ("Gradient Boosting", Pipeline([("imputer", SimpleImputer(strategy="median")), ("gradientboostingclassifier", GradientBoostingClassifier(random_state=42))])),
]
results = []
for name, pipe in candidates:
    r = tm.evaluate_model(name, pipe, Xtr, ytr, Xte, yte)
    r["test_rows"] = len(test)
    results.append(r)
    print(f"{name}: acc={r['test_accuracy']:.4f} logloss={r['test_log_loss']:.4f} auc={r['roc_auc']:.4f}")

best = sorted(results, key=lambda x: x["test_log_loss"])[0]
print("BEST by log loss:", best["name"])

out = HERE / "_artifacts_mech_model.json"
tm.export_model(best, {}, {}, df, out, 2015, 2020)
jm = json.loads(out.read_text())

# Recompute the EXPORTED logistic's own metrics from raw coef/intercept.
coef = np.array([jm["coefficients"][f] for f in jm["features"]])
b0 = jm["intercept"]
z = Xte.values @ coef + b0
proba = 1 / (1 + np.exp(-z))
pred = (proba >= 0.5).astype(int)
exported_metrics = {
    "test_accuracy": round(accuracy_score(yte, pred), 6),
    "test_log_loss": round(log_loss(yte, proba), 6),
    "brier_score": round(brier_score_loss(yte, proba), 6),
    "roc_auc": round(roc_auc_score(yte, proba), 6),
}

print(json.dumps({
    "best_model_name": best["name"],
    "json_metrics_block": jm["metrics"],
    "json_metrics_equal_best": {k: abs(jm["metrics"][k] - best[k]) < 1e-9 for k in jm["metrics"]},
    "exported_logistic_own_metrics": exported_metrics,
    "json_metrics_describe_exported_logistic": all(abs(jm["metrics"][k] - exported_metrics[k]) < 1e-3 for k in exported_metrics),
}, indent=2))
