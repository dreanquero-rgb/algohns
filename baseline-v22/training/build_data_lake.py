#!/usr/bin/env python3
"""
TennisForest GreenCode v21 - Multi-source data lake builder.

Goal
-----
Create ONE canonical, non-overlapping training dataset from multiple tennis data
sources. Match results are deduplicated into a single canonical match row;
external datasets enrich that row with odds, charting, ranking and player-stat
fields rather than creating duplicate matches.

Public/allowed automatic connectors included:
- Jeff Sackmann tennis_atp: master ATP results, rankings, player metadata.
- Tennis-Data.co.uk: historical results and fixed odds, best-effort downloader.
- Jeff Sackmann Match Charting Project: optional point/shot aggregate enrichment.
- Local user-provided CSV/XLSX/API exports: UTS, Tennis Abstract exports,
  API-Tennis exports, Kaggle exports, paid feeds, etc.

The script is intentionally conservative: if a source is unavailable or its
license/format is unclear, it is skipped and listed in data_lineage.json.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

JEFF_MATCH_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{year}.csv"
JEFF_PLAYER_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_players.csv"
JEFF_RANKING_URLS = [
    "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_rankings_70s.csv",
    "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_rankings_80s.csv",
    "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_rankings_90s.csv",
    "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_rankings_00s.csv",
    "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_rankings_10s.csv",
    "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_rankings_20s.csv",
]
MCP_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master"
MCP_MATCHES_URL = f"{MCP_BASE}/charting-m-matches.csv"
MCP_STATS_URLS = [
    f"{MCP_BASE}/charting-m-stats-Overview.csv",
    f"{MCP_BASE}/charting-m-stats-ServeBasics.csv",
    f"{MCP_BASE}/charting-m-stats-ReturnOutcomes.csv",
    f"{MCP_BASE}/charting-m-stats-SvBreakTotal.csv",
]

SOURCE_PRIORITY = {
    "jeff_sackmann_atp": 100,       # canonical result/master fields
    "tennis_data_odds": 80,         # odds enrichment, not result master unless missing
    "match_charting_project": 70,   # point/shot enrichment
    "api_tennis_export": 60,        # realtime/export enrichment
    "uts_export": 55,               # player stats/Elo enrichment if user exports legally
    "local_generic": 40,
}

@dataclass
class SourceReport:
    source: str
    status: str
    rows: int = 0
    note: str = ""
    license_note: str = ""


def slug(s: object) -> str:
    text = str(s or "").strip().lower()
    text = text.replace(".", " ").replace("-", " ").replace("_", " ")
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def player_key(name: object) -> str:
    return slug(name)


def tourney_key(name: object) -> str:
    text = slug(name)
    aliases = {
        "roland garros": "french open",
        "us open championships": "us open",
        "australian open championships": "australian open",
    }
    return aliases.get(text, text)


def parse_date(x: object) -> pd.Timestamp:
    if pd.isna(x):
        return pd.NaT
    s = str(x).strip()
    if re.fullmatch(r"\d{8}", s):
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    return pd.to_datetime(s, errors="coerce", dayfirst=False)


def match_identity(date, p1, p2, tournament="", round_name="") -> str:
    d = parse_date(date)
    dstr = "" if pd.isna(d) else d.strftime("%Y-%m-%d")
    names = sorted([player_key(p1), player_key(p2)])
    base = "|".join([dstr, tourney_key(tournament), slug(round_name), names[0], names[1]])
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:24]


def read_url_csv(url: str, cache_path: Path, timeout: int = 25) -> pd.DataFrame:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return pd.read_csv(cache_path)
    r = requests.get(url, timeout=timeout, headers={"user-agent": "TennisForestGreenCode/21"})
    r.raise_for_status()
    cache_path.write_bytes(r.content)
    return pd.read_csv(io.BytesIO(r.content))


def load_jeff_matches(start_year: int, end_year: int, cache_dir: Path, reports: List[SourceReport]) -> pd.DataFrame:
    frames = []
    for year in range(start_year, end_year + 1):
        url = JEFF_MATCH_URL.format(year=year)
        try:
            df = read_url_csv(url, cache_dir / "raw" / "jeff_sackmann" / f"atp_matches_{year}.csv")
            df["source"] = "jeff_sackmann_atp"
            df["source_priority"] = SOURCE_PRIORITY["jeff_sackmann_atp"]
            frames.append(df)
        except Exception as e:
            reports.append(SourceReport("jeff_sackmann_atp", "partial_skip", 0, f"{year}: {e}", "Public GitHub dataset; check repo license/terms before commercial use."))
    if not frames:
        reports.append(SourceReport("jeff_sackmann_atp", "failed", 0, "No match files downloaded."))
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    reports.append(SourceReport("jeff_sackmann_atp", "ok", len(out), "Loaded ATP match results/stats as canonical match backbone.", "Jeff Sackmann tennis_atp public GitHub dataset."))
    return canonicalize_jeff(out)


def canonicalize_jeff(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["match_date"] = df.get("tourney_date").map(parse_date)
    out["tournament"] = df.get("tourney_name")
    out["surface"] = df.get("surface")
    out["round"] = df.get("round")
    out["best_of"] = df.get("best_of")
    out["tourney_level"] = df.get("tourney_level")
    out["winner_name"] = df.get("winner_name")
    out["loser_name"] = df.get("loser_name")
    out["winner_rank"] = df.get("winner_rank")
    out["loser_rank"] = df.get("loser_rank")
    out["winner_rank_points"] = df.get("winner_rank_points")
    out["loser_rank_points"] = df.get("loser_rank_points")
    out["score"] = df.get("score")
    out["match_num"] = df.get("match_num")
    # Copy match-stat columns if they exist; these become pre-match rolling features in train_model.py.
    for c in df.columns:
        if c.startswith(("w_", "l_")) and c not in out.columns:
            out[c] = df[c]
    out["match_key"] = [match_identity(d, w, l, t, r) for d, w, l, t, r in zip(out["match_date"], out["winner_name"], out["loser_name"], out["tournament"], out["round"])]
    out["sources"] = "jeff_sackmann_atp"
    out["source_priority"] = SOURCE_PRIORITY["jeff_sackmann_atp"]
    return out.dropna(subset=["match_date", "winner_name", "loser_name"])


def load_tennis_data_odds(start_year: int, end_year: int, cache_dir: Path, local_dir: Path, reports: List[SourceReport]) -> pd.DataFrame:
    """Load Tennis-Data odds from local files first, then best-effort URL candidates.

    Tennis-Data historically publishes season files in CSV/XLS/XLSX. File naming has
    changed over time, so the downloader tries several candidates and never fails the
    build when a URL is unavailable.
    """
    files = []
    if local_dir.exists():
        files += list(local_dir.glob("**/*.csv")) + list(local_dir.glob("**/*.xls")) + list(local_dir.glob("**/*.xlsx"))

    frames = []
    for path in files:
        try:
            df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
            df["_source_file"] = str(path)
            frames.append(df)
        except Exception as e:
            reports.append(SourceReport("tennis_data_odds", "local_skip", 0, f"{path.name}: {e}"))

    # Best-effort candidates. Local files remain the recommended path because the site can be rate-limited/down.
    for y in range(start_year, end_year + 1):
        candidates = [
            f"https://www.tennis-data.co.uk/{y}/{y}.xlsx",
            f"https://www.tennis-data.co.uk/{y}/{y}.xls",
            f"https://www.tennis-data.co.uk/{y}/atp{str(y)[-2:]}.csv",
            f"https://www.tennis-data.co.uk/{y}/ATP{str(y)[-2:]}.csv",
        ]
        for url in candidates:
            cache_path = cache_dir / "raw" / "tennis_data" / Path(url).name
            if cache_path.exists() or not any(f"{y}" in str(f) for f in files):
                try:
                    if url.endswith(".csv"):
                        df = read_url_csv(url, cache_path, timeout=15)
                    else:
                        if cache_path.exists():
                            df = pd.read_excel(cache_path)
                        else:
                            r = requests.get(url, timeout=15, headers={"user-agent": "TennisForestGreenCode/21"})
                            if r.status_code != 200:
                                continue
                            cache_path.parent.mkdir(parents=True, exist_ok=True)
                            cache_path.write_bytes(r.content)
                            df = pd.read_excel(io.BytesIO(r.content))
                    df["_source_file"] = url
                    frames.append(df)
                    break
                except Exception:
                    continue

    if not frames:
        reports.append(SourceReport("tennis_data_odds", "missing", 0, "No odds files found. Put Tennis-Data CSV/XLSX in training/data/odds/.", "Tennis-Data says historical results and fixed odds files are free; verify terms for production use."))
        return pd.DataFrame()
    raw = pd.concat(frames, ignore_index=True)
    can = canonicalize_tennis_data(raw)
    reports.append(SourceReport("tennis_data_odds", "ok", len(can), "Loaded fixed-odds enrichment; merged by match_key.", "Historical odds/results dataset; respect Tennis-Data terms."))
    return can


def canonicalize_tennis_data(df: pd.DataFrame) -> pd.DataFrame:
    # Common Tennis-Data columns include Date, Tournament, Surface, Round, Winner, Loser, B365W/B365L, PSW/PSL, MaxW/MaxL, AvgW/AvgL.
    colmap = {slug(c): c for c in df.columns}
    def c(*names):
        for n in names:
            if slug(n) in colmap:
                return colmap[slug(n)]
        return None
    date_c, tour_c, surf_c, round_c = c("Date"), c("Tournament"), c("Surface"), c("Round")
    winner_c, loser_c = c("Winner"), c("Loser")
    if not all([date_c, winner_c, loser_c]):
        return pd.DataFrame()
    out = pd.DataFrame()
    out["match_date"] = df[date_c].map(parse_date)
    out["tournament"] = df[tour_c] if tour_c else ""
    out["surface"] = df[surf_c] if surf_c else ""
    out["round"] = df[round_c] if round_c else ""
    out["winner_name"] = df[winner_c]
    out["loser_name"] = df[loser_c]
    # Prefix odds fields so they cannot collide with match-result fields.
    odds_cols = [x for x in df.columns if re.search(r"(B365|PS|Max|Avg|EX|LB|CB|IW|SB|UB|odds|Odd)", str(x), re.I)]
    for oc in odds_cols:
        out[f"odds_{oc}"] = pd.to_numeric(df[oc], errors="coerce")
    out["match_key"] = [match_identity(d, w, l, t, r) for d, w, l, t, r in zip(out["match_date"], out["winner_name"], out["loser_name"], out["tournament"], out["round"])]
    out["sources"] = "tennis_data_odds"
    return out.dropna(subset=["match_date", "winner_name", "loser_name"])


def load_match_charting(cache_dir: Path, reports: List[SourceReport]) -> pd.DataFrame:
    try:
        m = read_url_csv(MCP_MATCHES_URL, cache_dir / "raw" / "mcp" / "charting-m-matches.csv", timeout=30)
    except Exception as e:
        reports.append(SourceReport("match_charting_project", "missing", 0, str(e), "CC BY-NC-SA 4.0; non-commercial only unless licensed."))
        return pd.DataFrame()
    colmap = {slug(c): c for c in m.columns}
    def c(*names):
        for n in names:
            if slug(n) in colmap: return colmap[slug(n)]
        return None
    # MCP has evolved; support several metadata shapes.
    date_c = c("date", "match date")
    p1_c = c("player 1", "player1", "winner")
    p2_c = c("player 2", "player2", "loser")
    tour_c = c("tournament", "event")
    surface_c = c("surface")
    if not all([date_c, p1_c, p2_c]):
        reports.append(SourceReport("match_charting_project", "format_skip", len(m), "Could not identify metadata columns.", "CC BY-NC-SA 4.0."))
        return pd.DataFrame()
    out = pd.DataFrame()
    out["match_date"] = m[date_c].map(parse_date)
    out["mcp_player1"] = m[p1_c]
    out["mcp_player2"] = m[p2_c]
    out["tournament"] = m[tour_c] if tour_c else ""
    out["surface"] = m[surface_c] if surface_c else ""
    out["match_key"] = [match_identity(d, p1, p2, t, "") for d, p1, p2, t in zip(out["match_date"], out["mcp_player1"], out["mcp_player2"], out["tournament"])]
    out["mcp_charted"] = 1
    out["sources"] = "match_charting_project"
    reports.append(SourceReport("match_charting_project", "ok", len(out), "Loaded charted-match coverage flag. Detailed stats can be joined from MCP stats files.", "CC BY-NC-SA 4.0; non-commercial attribution/share-alike."))
    return out.dropna(subset=["match_date", "mcp_player1", "mcp_player2"])


def load_local_enrichments(local_dir: Path, reports: List[SourceReport]) -> List[pd.DataFrame]:
    """Load generic user-provided enrichment files.

    Required columns for match-level join: date + player1/player2 OR winner/loser.
    Optional columns are kept with a local_ prefix unless already namespaced.
    """
    out = []
    if not local_dir.exists():
        reports.append(SourceReport("local_generic", "missing", 0, "No training/data/enrichments directory found."))
        return out
    for path in list(local_dir.glob("**/*.csv")) + list(local_dir.glob("**/*.xlsx")) + list(local_dir.glob("**/*.xls")):
        try:
            raw = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
            can = canonicalize_generic(raw, path.stem)
            if not can.empty:
                out.append(can)
                reports.append(SourceReport("local_generic", "ok", len(can), f"Loaded {path.name}."))
        except Exception as e:
            reports.append(SourceReport("local_generic", "skip", 0, f"{path.name}: {e}"))
    return out


def canonicalize_generic(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    colmap = {slug(c): c for c in df.columns}
    def c(*names):
        for n in names:
            if slug(n) in colmap: return colmap[slug(n)]
        return None
    date_c = c("date", "match_date", "tourney_date")
    p1_c = c("player1", "player_1", "p1", "winner", "winner_name")
    p2_c = c("player2", "player_2", "p2", "loser", "loser_name")
    if not all([date_c, p1_c, p2_c]):
        return pd.DataFrame()
    tour_c, round_c = c("tournament", "tourney_name", "event"), c("round")
    out = pd.DataFrame()
    out["match_date"] = df[date_c].map(parse_date)
    out["tournament"] = df[tour_c] if tour_c else ""
    out["round"] = df[round_c] if round_c else ""
    out["generic_p1"] = df[p1_c]
    out["generic_p2"] = df[p2_c]
    reserved = {date_c, p1_c, p2_c, tour_c, round_c}
    prefix = re.sub(r"[^a-z0-9]+", "_", source_name.lower()).strip("_")[:30] or "local"
    for col in df.columns:
        if col in reserved: continue
        name = str(col)
        if name in ["match_key", "sources"]: continue
        out[f"{prefix}_{name}"] = df[col]
    out["match_key"] = [match_identity(d, p1, p2, t, r) for d, p1, p2, t, r in zip(out["match_date"], out["generic_p1"], out["generic_p2"], out["tournament"], out["round"])]
    out["sources"] = prefix
    return out.dropna(subset=["match_date", "generic_p1", "generic_p2"])


def merge_enrichments(master: pd.DataFrame, enrichments: Iterable[pd.DataFrame]) -> pd.DataFrame:
    if master.empty:
        return master
    merged = master.copy()
    merged["sources"] = merged["sources"].fillna("jeff_sackmann_atp")
    for enr in enrichments:
        if enr is None or enr.empty or "match_key" not in enr.columns:
            continue
        # Drop duplicate enrichment rows by match_key to prevent one-to-many explosions.
        enr = enr.sort_values("match_key").drop_duplicates("match_key", keep="last")
        source_col = enr.get("sources", pd.Series(["unknown"] * len(enr)))
        join_cols = [c for c in enr.columns if c not in {"match_date", "winner_name", "loser_name", "generic_p1", "generic_p2", "mcp_player1", "mcp_player2", "tournament", "surface", "round", "sources"}]
        if "match_key" not in join_cols:
            join_cols = ["match_key"] + join_cols
        merged = merged.merge(enr[join_cols], on="match_key", how="left", suffixes=("", "_enr"))
        # Update source lineage where enrichment matched.
        hit = merged[join_cols[1]].notna() if len(join_cols) > 1 else pd.Series(False, index=merged.index)
        src = str(source_col.iloc[0]) if len(source_col) else "enrichment"
        merged.loc[hit, "sources"] = merged.loc[hit, "sources"].astype(str) + "+" + src
    return merged


def write_outputs(canonical: pd.DataFrame, cache_dir: Path, reports: List[SourceReport], start_year: int, end_year: int):
    out_dir = cache_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    canonical = canonical.sort_values(["match_date", "tournament", "match_num" if "match_num" in canonical.columns else "match_key"]).drop_duplicates("match_key", keep="first")
    canonical.to_csv(out_dir / "canonical_matches.csv", index=False)
    lineage = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "version": "v21-multisource-datalake",
        "start_year": start_year,
        "end_year": end_year,
        "canonical_rows": int(len(canonical)),
        "unique_match_keys": int(canonical["match_key"].nunique()) if "match_key" in canonical else 0,
        "sources": [asdict(r) for r in reports],
        "deduplication": {
            "unit": "match",
            "key": "sha1(date|tournament|round|sorted_player_names)",
            "policy": "Jeff Sackmann is result backbone; other sources enrich by match_key and never duplicate rows.",
        },
        "commercial_note": "Only use sources whose license/terms allow your intended use. MCP is non-commercial unless licensed separately.",
    }
    (out_dir / "data_lineage.json").write_text(json.dumps(lineage, indent=2), encoding="utf-8")
    print(f"Wrote {out_dir / 'canonical_matches.csv'} rows={len(canonical)}")
    print(f"Wrote {out_dir / 'data_lineage.json'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, default=2010)
    ap.add_argument("--end-year", type=int, default=datetime.utcnow().year)
    ap.add_argument("--cache-dir", type=Path, default=Path("data"))
    ap.add_argument("--include-mcp", action="store_true", help="Include Match Charting Project metadata enrichment. Non-commercial license.")
    ap.add_argument("--skip-odds-download", action="store_true", help="Use only local training/data/odds files for Tennis-Data odds.")
    args = ap.parse_args()

    reports: List[SourceReport] = []
    master = load_jeff_matches(args.start_year, args.end_year, args.cache_dir, reports)
    enrichments: List[pd.DataFrame] = []
    odds = load_tennis_data_odds(args.start_year, args.end_year, args.cache_dir, args.cache_dir / "odds", reports)
    if not odds.empty:
        enrichments.append(odds)
    if args.include_mcp:
        mcp = load_match_charting(args.cache_dir, reports)
        if not mcp.empty:
            enrichments.append(mcp)
    enrichments += load_local_enrichments(args.cache_dir / "enrichments", reports)
    canonical = merge_enrichments(master, enrichments)
    write_outputs(canonical, args.cache_dir, reports, args.start_year, args.end_year)


if __name__ == "__main__":
    main()
