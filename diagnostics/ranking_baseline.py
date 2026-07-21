#!/usr/bin/env python3
"""Phase 0.4 — "the better-ranked player wins" baseline on the SAME temporal
test split the trainer uses.

STATUS IN THIS ENVIRONMENT: not runnable. It needs the raw Jeff Sackmann
tennis_atp match data (or a canonical_matches.csv from build_data_lake.py).
Every external data host is denied (HTTP 403) by this session's egress policy,
including github.com / raw.githubusercontent.com. Run this WHERE THE DATA IS
REACHABLE (locally, or in an environment whose network policy allows GitHub):

    cd baseline-v22/training
    python ../../diagnostics/ranking_baseline.py --start-year 2015 --end-year 2025

It reuses train_model.py's own load_data + build_dataset so the split and the
`rank_log_diff` feature are byte-identical to training.
"""
import argparse, sys, json
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str((HERE / ".." / "baseline-v22" / "training").resolve()))
import train_model as tm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, default=2015)
    ap.add_argument("--end-year", type=int, default=2025)
    ap.add_argument("--cache-dir", type=Path, default=Path("data"))
    ap.add_argument("--test-fraction", type=float, default=0.2)
    args = ap.parse_args()

    df = tm.load_data(args.start_year, args.end_year, args.cache_dir)
    feature_df, _states, _h2h = tm.build_dataset(df)
    feature_df = feature_df.dropna(subset=["y"]).sort_values("date").reset_index(drop=True)

    cut = int(len(feature_df) * (1 - args.test_fraction))
    test = feature_df.iloc[cut:]
    y = test["y"].astype(int).values

    # rank_log_diff > 0  <=>  p1 is better ranked (lower rank number).
    rd = test["rank_log_diff"].values
    ed = test["elo_diff"].values  # bonus: Elo baseline (1.2b)

    def acc(pred_pos, y):
        # pred_pos: boolean "predict p1 wins"; ties resolved as 0.5 credit
        pred = np.where(pred_pos > 0, 1, np.where(pred_pos < 0, 0, -1))
        decided = pred >= 0
        correct = (pred[decided] == y[decided]).mean() if decided.any() else float("nan")
        with_ties = np.where(pred >= 0, (pred == y), 0.5).mean()
        return round(float(correct), 6), round(float(with_ties), 6), int((~decided).sum())

    rank_acc, rank_acc_ties, rank_ties = acc(rd, y)
    elo_acc, elo_acc_ties, elo_ties = acc(ed, y)

    print(json.dumps({
        "test_rows": int(len(test)),
        "baseline_ranking_accuracy_decided": rank_acc,
        "baseline_ranking_accuracy_incl_ties": rank_acc_ties,
        "baseline_ranking_ties": rank_ties,
        "baseline_elo_accuracy_decided": elo_acc,
        "baseline_elo_accuracy_incl_ties": elo_acc_ties,
        "note": "Compare against model.json metrics.test_accuracy. A model that does not clearly beat the ranking baseline has no usable edge.",
    }, indent=2))


if __name__ == "__main__":
    main()
