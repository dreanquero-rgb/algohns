#!/usr/bin/env python3
"""Turn an LSEG "Comparable Bonds" export into the bundled screener dataset.

The LSEG connector serves the chat session, not the deployed app, so real
LSEG data reaches the site as a committed snapshot (refreshable by re-running
this script on a new export). The snapshot keeps LSEG's own bid yield,
modified duration and G-spread so the screener can cross-check them against
the platform's ICMA engine.

    python scripts/build_lseg_dataset.py path/to/Comparable_Bonds.xlsx

Writes ``algohns/data/lseg_btp_comparables.csv``.

Licence note: LSEG market data carries redistribution terms. This snapshot
is for personal / educational use; do not republish it as a data feed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "algohns" / "data" / "lseg_btp_comparables.csv"

# The export carries a title row, a blank row, then a two-row header
# (Issuer/Coupon/... on one line, RIC/Bid/B Yld/... on the next).
HEADER_TOP, HEADER_SUB, DATA_START = 2, 3, 4  # 0-based row indices


def _short_issuer(name: str) -> str:
    n = (name or "").strip()
    if "Italy, Republic" in n:
        return "Italy"
    # "Palermo, Municipality of" -> "Palermo (Municipality)"
    for suffix in (", Municipality of", ", Province of", ", Autonomous Region of"):
        if n.endswith(suffix):
            return f"{n[: -len(suffix)]} ({suffix.strip(', ').replace('of', '').strip()})"
    return n


def build(src: str | Path) -> pd.DataFrame:
    raw = pd.read_excel(src, header=None)
    top = raw.iloc[HEADER_TOP].tolist()
    sub = raw.iloc[HEADER_SUB].tolist()
    cols = [
        (str(s).strip() if not pd.isna(s) and str(s).strip() else str(t).strip())
        for t, s in zip(top, sub)
    ]
    df = raw.iloc[DATA_START:].copy()
    df.columns = cols
    df = df.reset_index(drop=True)

    # Position-based rename: the export's column order is stable.
    by_pos = {
        0: "Issuer", 1: "Coupon", 2: "Maturity", 3: "ISIN", 5: "Bid",
        6: "LSEGYield", 7: "QuoteDate", 9: "GSpread", 13: "Currency",
        15: "LSEGModDur",
    }
    df = df.rename(columns={df.columns[i]: name for i, name in by_pos.items()
                            if i < len(df.columns)})

    df["Maturity"] = pd.to_datetime(df["Maturity"], errors="coerce")
    df["QuoteDate"] = pd.to_datetime(df["QuoteDate"], errors="coerce")
    for c in ("Coupon", "Bid", "LSEGYield", "GSpread", "LSEGModDur"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")

    df = df[df["ISIN"].notna() & df["Maturity"].notna()].copy()

    out = pd.DataFrame()
    out["ISIN"] = df["ISIN"].astype(str).str.strip()
    out["Name"] = [
        f"{_short_issuer(i)} {c:g}% {m:%m/%Y}" if pd.notna(c) else _short_issuer(i)
        for i, c, m in zip(df["Issuer"], df["Coupon"], df["Maturity"])
    ]
    out["Coupon"] = df["Coupon"]
    out["Maturity"] = df["Maturity"].dt.strftime("%Y-%m-%d")
    # A zero bid means "no live quote": store it blank so the engine skips it.
    out["Price"] = df["Bid"].where(df["Bid"] > 0)
    out["Currency"] = df["Currency"].fillna("EUR")
    # ISIN prefix carries the instrument shape: XS... eurobond, otherwise govt.
    out["Type"] = ["eurobond" if s.upper().startswith("XS") else "govt"
                   for s in out["ISIN"]]
    out["Country"] = "IT"  # every issuer in the export is domiciled in Italy
    out["LSEGYield"] = df["LSEGYield"].where(df["Bid"] > 0)
    out["LSEGModDur"] = df["LSEGModDur"].where(df["LSEGModDur"] > 0)
    out["GSpread"] = df["GSpread"].where(df["Bid"] > 0)
    out["QuoteDate"] = df["QuoteDate"].dt.strftime("%Y-%m-%d")

    return out.sort_values("Maturity").reset_index(drop=True)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    df = build(argv[1])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    priced = int(df["Price"].notna().sum())
    print(f"✓ {OUT.relative_to(ROOT)}  {len(df)} bonds ({priced} priced)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
