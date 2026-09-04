"""Module 1 data layer — European bond universe & live screener.

Builds a *RendimentiBTP / simpletoolsforinvestors*-style screener: it ingests the
full lists of bonds quoted on Borsa Italiana's MOT / EuroMOT markets (BTP, BOT,
CCT, plus Bund/OAT/Bonos/Eurobonds on EuroMOT) and, for every instrument,
computes gross & net yield-to-maturity, modified duration, current yield and
years-to-maturity via :mod:`algohns.modules.bond_engine`.

Data sources (in order of preference):
  1. The ``borsa-italiana-scraping`` package (if installed) for BTP + per-bond
     detail (``ottieni_scheda`` / ``ottieni_prezzo_corrente``).
  2. A self-contained requests+BeautifulSoup scraper of the public MOT list
     pages (works for every category, not just BTP).
  3. A bundled, clearly-labelled sample CSV so the screener is never empty when
     the network is unavailable (e.g. this sandbox blocks the exchange).

The HTML table structure was learned from Librefolio/borsaItaliana-scraping
(GPL-3.0); that library is used as an optional dependency, its code is not
vendored here.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from ..config import get_settings
from ..core.utils import is_available, lazy_import
from .bond_engine import TAX_PROFILES, Bond, BondEngine

_requests = lazy_import("requests", pip_name="requests", reason="query Borsa Italiana")
_bs4 = lazy_import("bs4", pip_name="beautifulsoup4", reason="parse Borsa Italiana HTML")

_SAMPLE_CSV = get_settings().data_dir / "bonds_sample.csv"

# Public MOT / EuroMOT list pages. The same table parser handles all of them.
MOT_LISTS: dict[str, dict] = {
    "BTP":  {"url": "https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/lista.html",  "country": "IT", "type": "govt", "freq": 2},
    "BOT":  {"url": "https://www.borsaitaliana.it/borsa/obbligazioni/mot/bot/lista.html",  "country": "IT", "type": "govt", "freq": 0},
    "CCT":  {"url": "https://www.borsaitaliana.it/borsa/obbligazioni/mot/cct/lista.html",  "country": "IT", "type": "govt_float", "freq": 2},
    "BTP€i": {"url": "https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp-indicizzati/lista.html", "country": "IT", "type": "govt_linker", "freq": 2},
    "EuroMOT": {"url": "https://www.borsaitaliana.it/borsa/obbligazioni/euromot/lista.html", "country": "EU", "type": "eurobond", "freq": 1},
}

_ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
# Country from ISIN prefix -> ISO-ish label.
_ISIN_COUNTRY = {
    "IT": "IT", "DE": "DE", "FR": "FR", "ES": "ES", "NL": "NL", "BE": "BE",
    "AT": "AT", "PT": "PT", "IE": "IE", "XS": "EU", "EU": "EU", "US": "US",
}


@dataclass
class ScreenerBond:
    isin: str
    name: str
    market: str
    country: str
    type: str
    price: float | None
    coupon: float | None       # annual %, e.g. 3.5
    maturity: date | None
    currency: str = "EUR"


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------
def _clean(txt: str) -> str:
    return " ".join(txt.split()).strip()


def _num(txt: str) -> float | None:
    txt = _clean(txt)
    if not txt or txt in ("-", "--", "n.a.", "N/A"):
        return None
    txt = txt.replace(",", "")  # borsaitaliana renders EN with thousands comma
    try:
        return float(txt)
    except ValueError:
        return None


def _parse_date(txt: str) -> date | None:
    txt = _clean(txt)
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None


def fetch_mot_list(market: str, timeout: int = 25) -> list[ScreenerBond]:
    """Scrape one MOT/EuroMOT list page into ScreenerBond rows.

    Raises on network/parse failure so the caller can fall back to samples.
    """
    if market not in MOT_LISTS:
        raise ValueError(f"unknown market {market}")
    if not (is_available(_requests) and is_available(_bs4)):
        raise RuntimeError("requests/beautifulsoup4 not installed")

    cfg = MOT_LISTS[market]
    settings = get_settings()
    headers = {"User-Agent": settings.sec_user_agent or "Mozilla/5.0 (Algohns)"}
    resp = _requests.get(cfg["url"], params={"lang": "en"}, headers=headers, timeout=timeout)
    resp.raise_for_status()
    soup = _bs4.BeautifulSoup(resp.text, "html.parser")

    table = soup.find("table")
    if table is None:
        raise RuntimeError(f"no table on {market} page")
    rows = table.find_all("tr")

    # Header column mapping (EN/IT tolerant).
    headers_cells = [_clean(th.get_text()).lower() for th in (rows[0].find_all(["th", "td"]) if rows else [])]
    idx = {"name": None, "isin": None, "price": None, "coupon": None, "maturity": None}
    for i, h in enumerate(headers_cells):
        if idx["name"] is None and any(k in h for k in ("name", "nome", "descr")):
            idx["name"] = i
        elif "isin" in h or "code" in h or "codice" in h:
            idx["isin"] = i
        elif idx["price"] is None and any(k in h for k in ("last", "ultimo", "price", "prezzo")):
            idx["price"] = i
        elif "coupon" in h or "cedola" in h:
            idx["coupon"] = i
        elif any(k in h for k in ("expiry", "scadenza", "maturity")):
            idx["maturity"] = i

    out: list[ScreenerBond] = []
    for tr in rows[1:]:
        tds = tr.find_all("td")
        if not tds:
            continue
        isin = _isin_from_row(tr, tds, idx["isin"])
        if not isin:
            continue
        name = _cell(tds, idx["name"]) or (tr.find("a").get_text(strip=True) if tr.find("a") else isin)
        out.append(
            ScreenerBond(
                isin=isin,
                name=_clean(name),
                market=market,
                country=_ISIN_COUNTRY.get(isin[:2], cfg["country"]),
                type=cfg["type"],
                price=_num(_cell(tds, idx["price"]) or ""),
                coupon=_num(_cell(tds, idx["coupon"]) or ""),
                maturity=_parse_date(_cell(tds, idx["maturity"]) or ""),
            )
        )
    return out


def _cell(tds, i) -> str | None:
    if i is not None and i < len(tds):
        return tds[i].get_text()
    return None


def _isin_from_row(tr, tds, isin_idx) -> str | None:
    # Prefer the dedicated ISIN column's link href, then any link, then text.
    candidates = []
    if isin_idx is not None and isin_idx < len(tds):
        candidates.append(tds[isin_idx])
    candidates.append(tr)
    for cell in candidates:
        for link in cell.find_all("a"):
            m = _ISIN_RE.search(str(link.get("href", "")).upper())
            if m:
                return m.group(1)
    m = _ISIN_RE.search(_clean(tr.get_text()).upper())
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Sample fallback
# ---------------------------------------------------------------------------
def load_sample() -> list[ScreenerBond]:
    """Load the bundled, clearly-labelled sample universe (offline fallback)."""
    if not _SAMPLE_CSV.exists():
        return []
    out: list[ScreenerBond] = []
    with _SAMPLE_CSV.open() as fh:
        for r in csv.DictReader(fh):
            out.append(
                ScreenerBond(
                    isin=r["isin"], name=r["name"], market=r.get("market", "SAMPLE"),
                    country=r.get("country", "IT"), type=r.get("type", "govt"),
                    price=float(r["price"]) if r.get("price") else None,
                    coupon=float(r["coupon"]) if r.get("coupon") else None,
                    maturity=_parse_date(r["maturity"]) if r.get("maturity") else None,
                    currency=r.get("currency", "EUR"),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Screener
# ---------------------------------------------------------------------------
class BondScreener:
    """Fetches the universe and computes the analytics table."""

    def __init__(self) -> None:
        self.engine = BondEngine()

    def load_universe(self, markets: list[str] | None = None) -> tuple[list[ScreenerBond], str]:
        """Return (bonds, source) where source is 'live' or 'sample'."""
        markets = markets or list(MOT_LISTS.keys())
        bonds: list[ScreenerBond] = []
        errors = 0
        for m in markets:
            try:
                bonds.extend(fetch_mot_list(m))
            except Exception:  # noqa: BLE001 - any failure falls back
                errors += 1
        if bonds:
            return bonds, "live"
        return load_sample(), "sample"

    def build_table(self, bonds: list[ScreenerBond], tax_key: str = "IT_GOV_WHITELIST",
                    settlement: date | None = None) -> pd.DataFrame:
        """Compute the full screener DataFrame for the given tax profile."""
        settlement = settlement or date.today()
        recs: list[dict] = []
        for b in bonds:
            rec = {
                "ISIN": b.isin, "Name": b.name, "Mkt": b.market, "Country": b.country,
                "Type": b.type, "Coupon%": b.coupon, "Price": b.price,
                "Maturity": b.maturity.isoformat() if b.maturity else None,
                "Years": round((b.maturity - settlement).days / 365.25, 2) if b.maturity else None,
                "Curr": b.currency,
            }
            # Compute yields only for priced, fixed-coupon bonds with a future maturity.
            if b.price and b.maturity and b.maturity > settlement and b.type in ("govt", "eurobond") and b.coupon is not None:
                try:
                    freq = MOT_LISTS.get(b.market, {}).get("freq", 2) or 2
                    bond = Bond(
                        face_value=100.0, coupon_rate=(b.coupon or 0) / 100.0,
                        frequency=int(freq) if freq else 1,
                        issue_date=date(max(b.maturity.year - 30, 1990), 1, 1),
                        maturity_date=b.maturity, settlement_date=settlement,
                        clean_price=float(b.price), isin=b.isin, name=b.name,
                    )
                    res = self.engine.analyse(bond, tax_key=tax_key)
                    rec["YTM%"] = round(res.ytm_gross * 100, 3)
                    rec["NetYTM%"] = round(res.ytm_net * 100, 3)
                    rec["ModDur"] = res.modified_duration
                    rec["Curr.Yield%"] = round((b.coupon / b.price) * 100, 3) if b.price else None
                    rec["Accrued"] = res.accrued_interest
                except Exception:  # noqa: BLE001
                    pass
            recs.append(rec)
        df = pd.DataFrame(recs)
        # Order columns nicely.
        preferred = ["ISIN", "Name", "Mkt", "Country", "Type", "Coupon%", "Price",
                     "YTM%", "NetYTM%", "Curr.Yield%", "ModDur", "Accrued", "Maturity", "Years", "Curr"]
        cols = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
        return df[cols] if not df.empty else df


def tax_profile_options() -> dict[str, str]:
    return {k: v.name for k, v in TAX_PROFILES.items()}
