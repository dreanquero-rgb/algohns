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
import math
import re
from typing import Sequence
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


_MISSING = {"-", "--", "---", "n.a.", "n/a", "n.d.", "nd", "", "—"}


def _num(txt: str, max_plausible: float | None = None) -> float | None:
    """Parse a number that may be in English *or* Italian formatting.

    Borsa Italiana is requested with ``lang=en`` but does not always honour
    it, and the two conventions collide: stripping commas unconditionally
    turns the Italian price ``101,25`` into ``10125`` — a hundredfold error
    that produces a plausible-looking number and a silently wrong yield,
    which is worse than a blank cell.

    The separator roles are inferred from the pattern rather than assumed:

    ==================  ============================  ==============
    input               reading                       result
    ==================  ============================  ==============
    ``1.234,56``        dot thousands, comma decimal  1234.56
    ``1,234.56``        comma thousands, dot decimal  1234.56
    ``101,25``          decimal comma (1-2 decimals)  101.25
    ``1,234``           thousands comma (3 digits)    1234.0
    ==================  ============================  ==============
    """
    txt = _clean(txt).replace("\u00a0", " ").replace(" ", "")
    if not txt or txt.lower() in _MISSING:
        return None
    txt = txt.replace("%", "").replace("+", "")
    raw = txt
    raw_seps = txt.count(",")

    has_dot, has_comma = "." in txt, "," in txt
    if has_dot and has_comma:
        # Whichever separator appears last is the decimal one.
        if txt.rfind(",") > txt.rfind("."):
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", "")
    elif has_comma:
        tail = txt.rsplit(",", 1)[1]
        # Exactly three trailing digits reads as a thousands group; one or
        # two reads as a decimal comma. Anything else is not a number.
        txt = txt.replace(",", "") if len(tail) == 3 else txt.replace(",", ".")
    elif has_dot:
        tail = txt.rsplit(".", 1)[1]
        if len(tail) == 3 and txt.count(".") >= 1 and len(txt.split(".")[0]) <= 3:
            # Ambiguous "1.234": treat as thousands only when the leading
            # group is short enough for that reading to make sense.
            txt = txt.replace(".", "")
    try:
        value = float(txt)
    except ValueError:
        return None

    # A three-digit group after a single separator is genuinely ambiguous:
    # "102,340" is one hundred two thousand in English and 102.34 in Italian.
    # The general rule above picks thousands, which is right for a count and
    # absurd for a bond price. Where the caller knows the domain bound, fall
    # back to the decimal reading rather than returning a number that is
    # wrong by a factor of a thousand and looks fine.
    if max_plausible is not None and value > max_plausible:
        single = (has_comma != has_dot) and (
            txt.count(".") + raw_seps == 1 or True
        )
        if single:
            alt_txt = raw.replace(".", "@").replace(",", ".").replace("@", "")
            try:
                alt = float(alt_txt)
            except ValueError:
                alt = None
            if alt is not None and alt <= max_plausible:
                return alt
    return value


# Month names in both languages, since the page may serve either.
_MONTHS = {
    "gen": 1, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "may": 5,
    "giu": 6, "jun": 6, "lug": 7, "jul": 7, "ago": 8, "aug": 8, "set": 9,
    "sep": 9, "ott": 10, "oct": 10, "nov": 11, "dic": 12, "dec": 12,
}
_DATE_SEPS = re.compile(r"[/\-.\s]+")


def _parse_date(txt: str) -> date | None:
    """Parse a maturity date across the formats the page may serve.

    The previous version accepted only four slash-separated numeric formats.
    A dash- or dot-separated date, or one with a month name, returned None —
    and a None maturity became a NaN ``Years`` column, which the screener's
    years filter then used to exclude every row. An empty table caused by a
    date format is very hard to diagnose from the UI, so this parser is
    deliberately permissive and the loader reports how many rows it failed
    on.
    """
    txt = _clean(txt)
    if not txt or txt.lower() in _MISSING:
        return None

    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y",
                "%m/%d/%Y", "%d/%m/%y", "%d-%m-%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue

    # Month-name forms: "01 lug 2034", "1-Jul-2034", "Jul 1 2034".
    parts = [p for p in _DATE_SEPS.split(txt) if p]
    if len(parts) == 3:
        nums = [p for p in parts if p.isdigit()]
        names = [p for p in parts if not p.isdigit()]
        if len(nums) == 2 and len(names) == 1:
            month = _MONTHS.get(names[0][:3].lower())
            if month:
                a, b = int(nums[0]), int(nums[1])
                day, year = (a, b) if b > 31 else (b, a)
                if year < 100:
                    year += 2000 if year < 70 else 1900
                try:
                    return date(year, month, day)
                except ValueError:
                    return None
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
                price=_num(_cell(tds, idx["price"]) or "", max_plausible=10_000),
                coupon=_num(_cell(tds, idx["coupon"]) or "", max_plausible=100),
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
# Every screener frame carries exactly these columns, in this order — including
# when the universe is empty — so downstream sorting/filtering is always safe.
SCREENER_COLUMNS: list[str] = [
    "ISIN", "Name", "Mkt", "Country", "Type", "Coupon%", "Price",
    "YTM%", "NetYTM%", "Curr.Yield%", "ModDur", "Accrued", "Maturity", "Years", "Curr",
]


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
                # Analytics columns are always present (None when not computable)
                # so downstream sorting/filtering never hits a missing column.
                "YTM%": None, "NetYTM%": None, "Curr.Yield%": None,
                "ModDur": None, "Accrued": None,
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
        df = pd.DataFrame(recs, columns=SCREENER_COLUMNS if not recs else None)
        # Guarantee the full schema even for an empty universe, then order it.
        for col in SCREENER_COLUMNS:
            if col not in df.columns:
                df[col] = None
        extra = [c for c in df.columns if c not in SCREENER_COLUMNS]
        return df[SCREENER_COLUMNS + extra]


def tax_profile_options() -> dict[str, str]:
    return {k: v.name for k, v in TAX_PROFILES.items()}

# ---------------------------------------------------------------------------
# Screener filtering
#
# Extracted from the Streamlit page because that is where the empty-table bug
# lived: UI glue is not covered by tests, so a filter that silently excluded
# every row went unnoticed. As pure functions these are testable.
# ---------------------------------------------------------------------------

DEFAULT_MAX_YEARS = 30.0


def screener_year_bounds(df: "pd.DataFrame", fallback: float = DEFAULT_MAX_YEARS) -> float:
    """Upper bound for the years-to-maturity slider.

    Returns `fallback` when the column is absent, empty, all-NaN, or
    non-positive. The previous inline expression was
    ``float(df["Years"].dropna().max() or 30)``, which looks like it guards
    against a missing column but does not: ``max()`` on an all-NaN column
    returns NaN, NaN is truthy, so ``or 30`` never fires and the bound became
    NaN. A NaN bound collapses the slider and makes the range filter
    all-False, which is how the screener came to report instruments loaded
    and none shown.
    """
    if "Years" not in df:
        return fallback
    known = df["Years"].dropna()
    if known.empty:
        return fallback
    top = float(known.max())
    if not math.isfinite(top) or top <= 0:
        return fallback
    # Round UP, never to nearest: the bound doubles as the range filter's
    # upper limit, and round(9.25, 1) is 9.2, which would put the
    # longest-dated bond just outside its own slider and filter it out.
    return math.ceil(top * 10.0) / 10.0


def filter_screener(
    df: "pd.DataFrame",
    countries: "Sequence[str] | None" = None,
    types: "Sequence[str] | None" = None,
    years_range: "tuple[float, float] | None" = None,
    min_net_ytm: float = 0.0,
) -> "pd.DataFrame":
    """Apply the screener's filters.

    Instruments whose maturity did not parse are kept while the lower bound of
    `years_range` sits at zero. Excluding them is what turned a date-format
    change into an empty table with nothing to explain it; keeping them makes
    a parsing problem show up as rows with blank yields, which is diagnosable.
    """
    if df.empty:
        return df.copy()

    mask = pd.Series(True, index=df.index)
    if countries is not None and "Country" in df:
        mask &= df["Country"].isin(list(countries))
    if types is not None and "Type" in df:
        mask &= df["Type"].isin(list(types))

    if years_range is not None and "Years" in df:
        lo, hi = years_range
        if math.isfinite(lo) and math.isfinite(hi):
            in_range = df["Years"].between(lo, hi)
            if lo <= 0.0:
                in_range = in_range | df["Years"].isna()
            mask &= in_range

    if min_net_ytm > 0 and "NetYTM%" in df:
        mask &= df["NetYTM%"].fillna(-99.0) >= min_net_ytm

    return df[mask].copy()
