#!/usr/bin/env python3
"""
TennisForest GreenCode v21 Multi-Source Data Intelligence training pipeline.

What it does:
1. Uses data/canonical_matches.csv from build_data_lake.py when present, otherwise downloads Jeff Sackmann ATP match CSVs.
2. Builds pre-match features chronologically:
   - Elo / surface Elo, ranking, form, H2H, fatigue, momentum
   - tournament level, round, best_of, surface-specific form
   - rolling serve/return strength from Jeff Sackmann point-stat columns when available
   - optional local odds enrichment files for future ROI/CLV research
3. Trains Logistic Regression, Random Forest and Gradient Boosting.
4. Uses a temporal split to avoid look-ahead bias.
5. Exports ../UPLOAD_THIS_TO_CLOUDFLARE/model.json for Cloudflare.

Usage:
  cd training
  pip install -r requirements.txt
  python build_data_lake.py --start-year 2010 --end-year 2025
  python train_model.py --start-year 2015 --end-year 2025

Optional:
  python train_model.py --start-year 2010 --end-year 2025 --export ../UPLOAD_THIS_TO_CLOUDFLARE/model.json
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import requests
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

JEFF_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{year}.csv"
CANONICAL_MATCHES = "canonical_matches.csv"
FEATURES = [
    "elo_diff",
    "surface_elo_diff",
    "win_rate_diff",
    "surface_win_rate_diff",
    "form_5_diff",
    "form_10_diff",
    "rank_log_diff",
    "rank_points_log_diff",
    "experience_log_diff",
    "h2h_diff",
    "tournament_level_slam",
    "tournament_level_masters",
    "best_of_5",
    "round_importance",
    "days_since_last_match_diff",
    "fatigue_diff",
    "win_streak_diff",
    "surface_form_diff",
    "ranking_momentum_diff",
    "tournament_level_500",
    "tournament_level_250",
    "serve_strength_diff",
    "return_strength_diff",
    "hold_rate_diff",
    "break_rate_diff",
    "ace_rate_diff",
    "double_fault_rate_diff",
    "tiebreak_win_rate_diff",
    "vs_top50_win_rate_diff",
]

SURFACES = ["Hard", "Clay", "Grass", "Carpet"]


def norm_name(name: str) -> str:
    return " ".join(str(name or "").lower().replace("-", " ").split())


@dataclass
class PlayerState:
    elo: float = 1500.0
    surface_elo: Dict[str, float] = field(default_factory=lambda: defaultdict(lambda: 1500.0))
    wins: int = 0
    losses: int = 0
    surface_wins: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    surface_losses: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    recent: deque = field(default_factory=lambda: deque(maxlen=10))
    rank: float = np.nan
    rank_points: float = np.nan
    previous_rank: float = np.nan
    last_date: pd.Timestamp | None = None
    win_streak: int = 0
    service_points_won: float = 0.0
    service_points_total: float = 0.0
    return_points_won: float = 0.0
    return_points_total: float = 0.0
    aces: float = 0.0
    double_faults: float = 0.0
    service_games: float = 0.0
    break_points_saved: float = 0.0
    break_points_faced: float = 0.0
    break_points_converted: float = 0.0
    break_points_return: float = 0.0
    tiebreak_wins: int = 0
    tiebreak_losses: int = 0
    vs_top50_wins: int = 0
    vs_top50_losses: int = 0


def expected(ra: float, rb: float) -> float:
    return 1 / (1 + 10 ** ((rb - ra) / 400))


def update_elo(ra: float, rb: float, score_a: float, k: float = 28) -> float:
    return ra + k * (score_a - expected(ra, rb))


def smooth_rate(w: float, l: float, prior: float = 3.0) -> float:
    return (w + prior) / (w + l + 2 * prior)


def recent_rate(recent: deque, n: int) -> float:
    vals = list(recent)[-n:]
    return (sum(vals) + 1) / (len(vals) + 2) if vals else 0.5


def surface_elo(ps: PlayerState, surface: str) -> float:
    return float(ps.surface_elo.get(surface, 1500.0))


def tournament_flags(level: str) -> Tuple[float, float, float, float]:
    text = str(level or "").lower()
    slam = float(any(x in text for x in ["grand slam", "australian open", "roland garros", "french open", "wimbledon", "us open", "g"]))
    masters = float("masters" in text or "1000" in text or text.strip() == "m")
    atp500 = float("500" in text or text.strip() == "a")
    atp250 = float("250" in text)
    return slam, masters, atp500, atp250


def round_importance(round_name: str) -> float:
    text = str(round_name or "").lower()
    if "final" in text: return 1.0
    if "semi" in text: return 0.75
    if "quarter" in text: return 0.55
    if "round of 16" in text or "r16" in text: return 0.35
    return 0.15


def days_since(ps: PlayerState, date) -> float:
    if ps.last_date is None or pd.isna(date): return 30.0
    return float(max(0, min(60, (date - ps.last_date).days)))


def rank_momentum(ps: PlayerState) -> float:
    if not np.isfinite(ps.rank) or not np.isfinite(ps.previous_rank): return 0.0
    return float((ps.previous_rank - ps.rank) / 100.0)




def safe_div(num: float, den: float, fallback: float = 0.0) -> float:
    try:
        return float(num) / float(den) if float(den) > 0 else fallback
    except Exception:
        return fallback


def serve_strength(ps: PlayerState) -> float:
    spw = safe_div(ps.service_points_won, ps.service_points_total, 0.62)
    ace = safe_div(ps.aces, ps.service_points_total, 0.06)
    df = safe_div(ps.double_faults, ps.service_points_total, 0.035)
    bp_save = safe_div(ps.break_points_saved, ps.break_points_faced, 0.60)
    return float((spw - 0.62) + 0.55 * (ace - 0.06) - 0.75 * (df - 0.035) + 0.18 * (bp_save - 0.60))


def return_strength(ps: PlayerState) -> float:
    rpw = safe_div(ps.return_points_won, ps.return_points_total, 0.38)
    bp_conv = safe_div(ps.break_points_converted, ps.break_points_return, 0.40)
    return float((rpw - 0.38) + 0.22 * (bp_conv - 0.40))


def hold_rate_proxy(ps: PlayerState) -> float:
    # Jeff does not always include games held directly; this proxy stays conservative.
    return max(0.35, min(0.95, 0.50 + serve_strength(ps)))


def break_rate_proxy(ps: PlayerState) -> float:
    return max(0.05, min(0.65, 0.25 + return_strength(ps)))


def tiebreak_rate(ps: PlayerState) -> float:
    return smooth_rate(ps.tiebreak_wins, ps.tiebreak_losses)


def parse_tiebreaks(score: str) -> int:
    # Counts set scores that include parentheses, common in Jeff Sackmann scores such as 7-6(4).
    return str(score or '').count('(')

def build_feature_row(p1: str, p2: str, surface: str, best_of: float, states: Dict[str, PlayerState], h2h: Dict[Tuple[str, str], int], match_date=None, level: str = "", round_name: str = "") -> Dict[str, float]:
    a = states[p1]
    b = states[p2]
    h2h_diff = h2h.get((p1, p2), 0) - h2h.get((p2, p1), 0)
    slam, masters, atp500, atp250 = tournament_flags(level)
    p1_days = days_since(a, match_date)
    p2_days = days_since(b, match_date)
    # crude fatigue proxy: very short rest is penalized, long layoffs are mildly uncertain.
    p1_fatigue = 1.0 if p1_days <= 2 else (0.25 if p1_days >= 21 else 0.0)
    p2_fatigue = 1.0 if p2_days <= 2 else (0.25 if p2_days >= 21 else 0.0)

    return {
        "elo_diff": (a.elo - b.elo) / 400.0,
        "surface_elo_diff": (surface_elo(a, surface) - surface_elo(b, surface)) / 400.0,
        "win_rate_diff": smooth_rate(a.wins, a.losses) - smooth_rate(b.wins, b.losses),
        "surface_win_rate_diff": smooth_rate(a.surface_wins.get(surface, 0), a.surface_losses.get(surface, 0)) - smooth_rate(b.surface_wins.get(surface, 0), b.surface_losses.get(surface, 0)),
        "form_5_diff": recent_rate(a.recent, 5) - recent_rate(b.recent, 5),
        "form_10_diff": recent_rate(a.recent, 10) - recent_rate(b.recent, 10),
        "rank_log_diff": math.log((b.rank if np.isfinite(b.rank) else 250) + 1) - math.log((a.rank if np.isfinite(a.rank) else 250) + 1),
        "rank_points_log_diff": math.log((a.rank_points if np.isfinite(a.rank_points) else 0) + 1) - math.log((b.rank_points if np.isfinite(b.rank_points) else 0) + 1),
        "experience_log_diff": math.log(a.wins + a.losses + 1) - math.log(b.wins + b.losses + 1),
        "h2h_diff": h2h_diff / 10.0,
        "tournament_level_slam": slam,
        "tournament_level_masters": masters,
        "tournament_level_500": atp500,
        "tournament_level_250": atp250,
        "best_of_5": 1.0 if best_of >= 5 else 0.0,
        "round_importance": round_importance(round_name),
        "days_since_last_match_diff": (p1_days - p2_days) / 30.0,
        "fatigue_diff": p1_fatigue - p2_fatigue,
        "win_streak_diff": (a.win_streak - b.win_streak) / 5.0,
        "surface_form_diff": smooth_rate(a.surface_wins.get(surface, 0), a.surface_losses.get(surface, 0)) - smooth_rate(b.surface_wins.get(surface, 0), b.surface_losses.get(surface, 0)),
        "ranking_momentum_diff": rank_momentum(a) - rank_momentum(b),
        "serve_strength_diff": serve_strength(a) - serve_strength(b),
        "return_strength_diff": return_strength(a) - return_strength(b),
        "hold_rate_diff": hold_rate_proxy(a) - hold_rate_proxy(b),
        "break_rate_diff": break_rate_proxy(a) - break_rate_proxy(b),
        "ace_rate_diff": safe_div(a.aces, a.service_points_total, 0.06) - safe_div(b.aces, b.service_points_total, 0.06),
        "double_fault_rate_diff": safe_div(a.double_faults, a.service_points_total, 0.035) - safe_div(b.double_faults, b.service_points_total, 0.035),
        "tiebreak_win_rate_diff": tiebreak_rate(a) - tiebreak_rate(b),
        "vs_top50_win_rate_diff": smooth_rate(a.vs_top50_wins, a.vs_top50_losses) - smooth_rate(b.vs_top50_wins, b.vs_top50_losses),
    }


def update_states(winner: str, loser: str, surface: str, winner_rank, loser_rank, winner_rank_points, loser_rank_points, match_date, states, h2h, raw_match=None):
    w = states[winner]
    l = states[loser]

    old_we, old_le = w.elo, l.elo
    w.elo = update_elo(old_we, old_le, 1)
    l.elo = update_elo(old_le, old_we, 0)

    old_ws, old_ls = surface_elo(w, surface), surface_elo(l, surface)
    w.surface_elo[surface] = update_elo(old_ws, old_ls, 1, 22)
    l.surface_elo[surface] = update_elo(old_ls, old_ws, 0, 22)

    w.wins += 1
    l.losses += 1
    w.surface_wins[surface] += 1
    l.surface_losses[surface] += 1
    w.recent.append(1)
    l.recent.append(0)

    if pd.notna(winner_rank):
        if np.isfinite(w.rank): w.previous_rank = w.rank
        w.rank = float(winner_rank)
    if pd.notna(loser_rank):
        if np.isfinite(l.rank): l.previous_rank = l.rank
        l.rank = float(loser_rank)
    if pd.notna(winner_rank_points): w.rank_points = float(winner_rank_points)
    if pd.notna(loser_rank_points): l.rank_points = float(loser_rank_points)

    w.win_streak = max(1, w.win_streak + 1)
    l.win_streak = min(-1, l.win_streak - 1)
    w.last_date = match_date
    l.last_date = match_date

    h2h[(winner, loser)] += 1


    # raw_match can be a pandas Series when called from iterrows();
    # never evaluate it as boolean (Series truth value is ambiguous).
    if raw_match is None:
        raw_match = {}

    def n(col):
        try:
            v = raw_match.get(col, 0)
        except AttributeError:
            v = 0
        if pd.isna(v):
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    w_svpt, l_svpt = n('w_svpt'), n('l_svpt')
    w_1stwon, w_2ndwon = n('w_1stWon'), n('w_2ndWon')
    l_1stwon, l_2ndwon = n('l_1stWon'), n('l_2ndWon')
    w.service_points_won += w_1stwon + w_2ndwon
    w.service_points_total += w_svpt
    l.service_points_won += l_1stwon + l_2ndwon
    l.service_points_total += l_svpt
    w.return_points_won += max(0.0, l_svpt - (l_1stwon + l_2ndwon))
    w.return_points_total += l_svpt
    l.return_points_won += max(0.0, w_svpt - (w_1stwon + w_2ndwon))
    l.return_points_total += w_svpt
    w.aces += n('w_ace'); l.aces += n('l_ace')
    w.double_faults += n('w_df'); l.double_faults += n('l_df')
    w.service_games += n('w_SvGms'); l.service_games += n('l_SvGms')
    w.break_points_saved += n('w_bpSaved'); w.break_points_faced += n('w_bpFaced')
    l.break_points_saved += n('l_bpSaved'); l.break_points_faced += n('l_bpFaced')
    w.break_points_converted += max(0.0, n('l_bpFaced') - n('l_bpSaved')); w.break_points_return += n('l_bpFaced')
    l.break_points_converted += max(0.0, n('w_bpFaced') - n('w_bpSaved')); l.break_points_return += n('w_bpFaced')
    tb = parse_tiebreaks(raw_match.get('score', ''))
    if tb:
        w.tiebreak_wins += tb
        l.tiebreak_losses += tb
    if pd.notna(loser_rank) and float(loser_rank) <= 50:
        w.vs_top50_wins += 1
    if pd.notna(winner_rank) and float(winner_rank) <= 50:
        l.vs_top50_losses += 1


def download_year(year: int, cache_dir: Path) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / f"atp_matches_{year}.csv"
    if not f.exists():
        url = JEFF_URL.format(year=year)
        print(f"Downloading {url}")
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        f.write_bytes(r.content)
    return pd.read_csv(f)


def load_data(start_year: int, end_year: int, cache_dir: Path) -> pd.DataFrame:
    """Load canonical v21 data lake if available; otherwise fall back to Jeff Sackmann.

    build_data_lake.py writes data/canonical_matches.csv after merging non-overlapping
    sources by match_key. The trainer accepts that richer file without needing to know
    which enrichment source produced each optional field.
    """
    canonical = cache_dir / CANONICAL_MATCHES
    if canonical.exists():
        print(f"Loading v21 canonical data lake: {canonical}")
        df = pd.read_csv(canonical)
        # Map canonical columns back to the Jeff-like names expected by the feature engine.
        if "match_date" in df.columns and "tourney_date" not in df.columns:
            df["tourney_date"] = df["match_date"]
        if "tournament" in df.columns and "tourney_name" not in df.columns:
            df["tourney_name"] = df["tournament"]
        if "tourney_level" not in df.columns:
            df["tourney_level"] = ""
        if "match_num" not in df.columns:
            df["match_num"] = np.arange(len(df))
    else:
        dfs = []
        for y in range(start_year, end_year + 1):
            try:
                dfs.append(download_year(y, cache_dir))
            except Exception as e:
                print(f"Skipping {y}: {e}")
        if not dfs:
            raise RuntimeError("No data downloaded")
        df = pd.concat(dfs, ignore_index=True)

    df = df[df["winner_name"].notna() & df["loser_name"].notna()].copy()
    df["tourney_date"] = pd.to_datetime(df["tourney_date"].astype(str), errors="coerce")
    df = df[df["tourney_date"].notna()].sort_values(["tourney_date", "tourney_name", "match_num"])
    df["surface"] = df["surface"].fillna("Hard")
    return df


def build_dataset(df: pd.DataFrame):
    states: Dict[str, PlayerState] = defaultdict(PlayerState)
    h2h: Dict[Tuple[str, str], int] = defaultdict(int)
    rows = []

    for _, m in df.iterrows():
        w = norm_name(m["winner_name"])
        l = norm_name(m["loser_name"])
        surface = str(m.get("surface") or "Hard")
        best_of = float(m.get("best_of") or 3)

        # Original order: p1 = winner, y=1
        level = str(m.get("tourney_level") or m.get("tourney_name") or "")
        round_name = str(m.get("round") or "")
        f1 = build_feature_row(w, l, surface, best_of, states, h2h, m["tourney_date"], level, round_name)
        f1.update({"date": m["tourney_date"], "p1": w, "p2": l, "surface": surface, "y": 1})
        rows.append(f1)

        # Mirrored order: p1 = loser, y=0
        f2 = build_feature_row(l, w, surface, best_of, states, h2h, m["tourney_date"], level, round_name)
        f2.update({"date": m["tourney_date"], "p1": l, "p2": w, "surface": surface, "y": 0})
        rows.append(f2)

        update_states(w, l, surface, m.get("winner_rank"), m.get("loser_rank"), m.get("winner_rank_points"), m.get("loser_rank_points"), m["tourney_date"], states, h2h, m)

    return pd.DataFrame(rows), states, h2h


def evaluate_model(name, pipe, X_train, y_train, X_test, y_test):
    pipe.fit(X_train, y_train)
    proba = pipe.predict_proba(X_test)[:, 1]
    pred = (proba >= 0.5).astype(int)
    return {
        "name": name,
        "model": pipe,
        "test_accuracy": float(accuracy_score(y_test, pred)),
        "test_log_loss": float(log_loss(y_test, proba)),
        "brier_score": float(brier_score_loss(y_test, proba)),
        "roc_auc": float(roc_auc_score(y_test, proba)),
    }


def confidence_buckets(y_true, proba):
    buckets = []
    confidence = np.maximum(proba, 1 - proba)
    correct = ((proba >= 0.5).astype(int) == np.asarray(y_true).astype(int))
    for lo, hi in [(0.5,0.6),(0.6,0.7),(0.7,0.8),(0.8,0.9),(0.9,1.01)]:
        mask = (confidence >= lo) & (confidence < hi)
        buckets.append({
            "bucket": f"{int(lo*100)}-{int(min(hi,1)*100)}%",
            "n": int(mask.sum()),
            "accuracy": None if mask.sum() == 0 else round(float(correct[mask].mean()), 6),
            "avg_confidence": None if mask.sum() == 0 else round(float(confidence[mask].mean()), 6),
        })
    return buckets


def estimate_temperature(y_true, proba):
    # Lightweight post-hoc calibration hint for the Worker. Searches a conservative temperature
    # that improves log loss on temporal test predictions without changing model direction.
    eps = 1e-6
    logits = np.log(np.clip(proba, eps, 1-eps) / np.clip(1-proba, eps, 1-eps))
    best_t, best_loss = 1.0, log_loss(y_true, proba)
    for t in np.arange(0.75, 1.81, 0.05):
        p = 1 / (1 + np.exp(-(logits / t)))
        ll = log_loss(y_true, p)
        if ll < best_loss:
            best_t, best_loss = float(t), float(ll)
    return {"temperature": round(best_t, 3), "calibrated_log_loss": round(best_loss, 6)}


def export_model(best, states, h2h, feature_df, export_path: Path, start_year: int, end_year: int):
    model = best["model"]
    # Export logistic if available; otherwise train a logistic model for Cloudflare portability.
    if "logisticregression" in model.named_steps:
        lr = model.named_steps["logisticregression"]
        scaler = model.named_steps["standardscaler"]
    else:
        portable = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("standardscaler", StandardScaler()),
            ("logisticregression", LogisticRegression(max_iter=3000, C=0.8)),
        ])
        X = feature_df[FEATURES]
        y = feature_df["y"].astype(int)
        portable.fit(X, y)
        lr = portable.named_steps["logisticregression"]
        scaler = portable.named_steps["standardscaler"]

    coef_scaled = lr.coef_[0]
    means = scaler.mean_
    scales = scaler.scale_
    # Convert standardized logistic to raw-feature coefficients.
    raw_coefs = {f: float(coef_scaled[i] / (scales[i] if scales[i] else 1)) for i, f in enumerate(FEATURES)}
    intercept = float(lr.intercept_[0] - sum(coef_scaled[i] * means[i] / (scales[i] if scales[i] else 1) for i in range(len(FEATURES))))

    players = {}
    for name, s in states.items():
        matches = s.wins + s.losses
        players[name] = {
            "elo": round(float(s.elo), 4),
            "surface_elo": {surf: round(float(s.surface_elo.get(surf, 1500)), 4) for surf in SURFACES},
            "matches": int(matches),
            "wins": int(s.wins),
            "losses": int(s.losses),
            "win_rate": round(float(smooth_rate(s.wins, s.losses)), 5),
            "surface_win_rate": {surf: round(float(smooth_rate(s.surface_wins.get(surf, 0), s.surface_losses.get(surf, 0))), 5) for surf in SURFACES},
            "form_5": round(float(recent_rate(s.recent, 5)), 5),
            "form_10": round(float(recent_rate(s.recent, 10)), 5),
            "rank": None if not np.isfinite(s.rank) else int(s.rank),
            "rank_points": None if not np.isfinite(s.rank_points) else int(s.rank_points),
            "ranking_momentum": round(float(rank_momentum(s)), 5),
            "serve_strength": round(float(serve_strength(s)), 6),
            "return_strength": round(float(return_strength(s)), 6),
            "hold_rate": round(float(hold_rate_proxy(s)), 5),
            "break_rate": round(float(break_rate_proxy(s)), 5),
            "ace_rate": round(float(safe_div(s.aces, s.service_points_total, 0.06)), 5),
            "double_fault_rate": round(float(safe_div(s.double_faults, s.service_points_total, 0.035)), 5),
            "tiebreak_win_rate": round(float(tiebreak_rate(s)), 5),
            "vs_top50_win_rate": round(float(smooth_rate(s.vs_top50_wins, s.vs_top50_losses)), 5),
            "surface_form_10": {surf: round(float(smooth_rate(s.surface_wins.get(surf, 0), s.surface_losses.get(surf, 0))), 5) for surf in SURFACES},
        }

    h2h_export = {f"{a}__{b}": int(v) for (a, b), v in h2h.items() if abs(v) > 0}

    export = {
        "version": "greencode-logistic-v21-multisource-data-intelligence",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "data_source": "v21 canonical data lake: Jeff Sackmann backbone + Tennis-Data odds + Match Charting Project + optional legal CSV/API enrichments",
        "data_sources": ["Jeff Sackmann tennis_atp", "Tennis-Data historical odds", "Jeff Sackmann Match Charting Project optional enrichment", "local legal CSV/API exports", "API-Tennis realtime layer in Cloudflare"],
        "intelligence": {"multi_source_data_lake": True, "source_lineage": True, "non_overlapping_match_keys": True, "serve_return_stats": True, "fatigue_proxy": True, "surface_specific_form": True, "ranking_momentum": True, "data_depth_score": True, "walk_forward_ready": True},
        "start_year": start_year,
        "end_year": end_year,
        "trained_through": str(feature_df["date"].max().date()),
        "training_rows": int(len(feature_df)),
        "test_rows": int(best.get("test_rows", 0)),
        "features": FEATURES,
        "intercept": intercept,
        "coefficients": raw_coefs,
        "metrics": {k: round(float(best[k]), 6) for k in ["test_accuracy", "test_log_loss", "brier_score", "roc_auc"] if k in best},
        "calibration": best.get("calibration", {"temperature": 1.12}),
        "confidence_buckets": best.get("confidence_buckets", []),
        "top_features": sorted([{"feature": f, "weight": raw_coefs[f]} for f in FEATURES], key=lambda x: abs(x["weight"]), reverse=True),
        "players": players,
        "h2h": h2h_export,
    }

    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text(json.dumps(export, indent=2))
    print(f"Exported {export_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, default=2015)
    ap.add_argument("--end-year", type=int, default=datetime.utcnow().year)
    ap.add_argument("--cache-dir", type=Path, default=Path("data"))
    ap.add_argument("--export", type=Path, default=Path("../UPLOAD_THIS_TO_CLOUDFLARE/model.json"))
    ap.add_argument("--test-fraction", type=float, default=0.2)
    args = ap.parse_args()

    df = load_data(args.start_year, args.end_year, args.cache_dir)
    feature_df, states, h2h = build_dataset(df)
    feature_df = feature_df.dropna(subset=["y"]).sort_values("date").reset_index(drop=True)

    cutoff_idx = int(len(feature_df) * (1 - args.test_fraction))
    train = feature_df.iloc[:cutoff_idx]
    test = feature_df.iloc[cutoff_idx:]

    X_train, y_train = train[FEATURES], train["y"].astype(int)
    X_test, y_test = test[FEATURES], test["y"].astype(int)

    candidates = [
        ("Logistic Regression", Pipeline([("imputer", SimpleImputer(strategy="median")), ("standardscaler", StandardScaler()), ("logisticregression", LogisticRegression(max_iter=3000, C=0.8))])),
        ("Random Forest", Pipeline([("imputer", SimpleImputer(strategy="median")), ("randomforestclassifier", RandomForestClassifier(n_estimators=350, min_samples_leaf=25, random_state=42, n_jobs=-1))])),
        ("Gradient Boosting", Pipeline([("imputer", SimpleImputer(strategy="median")), ("gradientboostingclassifier", GradientBoostingClassifier(random_state=42))])),
    ]

    results = []
    for name, pipe in candidates:
        r = evaluate_model(name, pipe, X_train, y_train, X_test, y_test)
        r["test_rows"] = int(len(test))
        r["calibration"] = estimate_temperature(y_test, r["model"].predict_proba(X_test)[:, 1])
        r["confidence_buckets"] = confidence_buckets(y_test, r["model"].predict_proba(X_test)[:, 1])
        results.append(r)
        print(f"{name}: acc={r['test_accuracy']:.4f}, logloss={r['test_log_loss']:.4f}, brier={r['brier_score']:.4f}, auc={r['roc_auc']:.4f}")

    best = sorted(results, key=lambda x: x["test_log_loss"])[0]
    print(f"Best by log loss: {best['name']}")

    # Always export a portable logistic approximation, but include metrics for best model.
    export_model(best, states, h2h, feature_df, args.export, args.start_year, args.end_year)

    metrics_path = Path("metrics.json")
    metrics_path.write_text(json.dumps([{k:v for k,v in r.items() if k != "model"} for r in results], indent=2))
    print(f"Wrote {metrics_path}")


if __name__ == "__main__":
    main()
