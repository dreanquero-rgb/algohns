"""Bond data sources behind one interface.

The screener does not care where a BTP came from, so each source is a
`BondProvider` returning `ScreenerBond` rows:

  * ``BorsaItalianaProvider`` — free MOT/EuroMOT scrape (the existing one)
  * ``RendimentiBtpProvider`` — free rendimentibtp.it scrape (all BTPs)
  * ``CsvImportProvider``     — a pasted/uploaded CSV (manual fallback)
  * ``SampleProvider``        — the bundled offline universe

A note on why the CSV import exists: rendimentibtp.it and Borsa Italiana are
both unreachable from some hosts (this build's proxy blocks them), so a
manual paste/upload is the reliable fallback when a scrape returns nothing.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date
from typing import Protocol, runtime_checkable

from algohns.modules.bond_data import (
    ScreenerBond,
    _clean,
    _infer_column,
    _ISIN_RE,
    _looks_like_future_date,
    _looks_like_price,
    _num,
    _parse_date,
    coupon_from_name,
    fetch_mot_list,
    load_lseg_bundled,
    load_sample,
    maturity_from_name,
)

log = logging.getLogger(__name__)

try:
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None  # type: ignore
try:
    from bs4 import BeautifulSoup as _BS  # type: ignore
except ImportError:  # pragma: no cover
    _BS = None  # type: ignore

RENDIMENTIBTP_URL = "https://www.rendimentibtp.it/lista-tutti-i-btp/"


@runtime_checkable
class BondProvider(Protocol):
    key: str
    label: str

    def available(self) -> bool: ...
    def fetch(self, markets: list[str] | None = None) -> list[ScreenerBond]: ...


# ---------------------------------------------------------------------------
# Generic HTML table -> bonds
#
# Reuses the resilient content-inference the Borsa Italiana fix proved out:
# pick the table with the most rows/ISINs, infer the price and maturity
# columns from their values when the header cannot be mapped, and fall back
# to the instrument name for coupon and maturity (Italian govvie names carry
# both). rendimentibtp lists by description, not ISIN, so name-only rows are
# kept with a synthesised id rather than dropped.
# ---------------------------------------------------------------------------
def _synth_id(name: str, country: str = "IT") -> str:
    slug = re.sub(r"[^A-Z0-9]", "", name.upper())[:12].ljust(4, "X")
    return f"{country}~{slug}"


def _looks_like_name(txt: str) -> bool:
    t = _clean(txt)
    return len(t) >= 4 and any(c.isalpha() for c in t) and not _looks_like_future_date(t)


def _looks_like_coupon(txt: str) -> bool:
    v = _num(txt, max_plausible=100)
    return v is not None and 0.0 <= v <= 20.0


def parse_bond_table(
    html: str,
    *,
    market: str,
    country: str = "IT",
    bond_type: str = "govt",
    require_isin: bool = False,
) -> list[ScreenerBond]:
    """Best-effort extraction of a bond list from an arbitrary HTML page."""
    if _BS is None:
        raise RuntimeError("beautifulsoup4 not installed")
    soup = _BS(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        raise RuntimeError(f"no table on {market} page")

    def score(t) -> int:
        return len(_ISIN_RE.findall(_clean(t.get_text()).upper())) + len(t.find_all("tr"))

    rows = max(tables, key=score).find_all("tr")

    grid: list[list[str]] = []
    trs: list = []
    for tr in rows:
        tds = tr.find_all(["td"])
        if len(tds) < 2:
            continue
        grid.append([_clean(td.get_text()) for td in tds])
        trs.append(tds)
    if not grid:
        raise RuntimeError(f"no data rows on {market} page")

    name_col = _infer_column(grid, _looks_like_name, min_share=0.4) or 0
    price_col = _infer_column(grid, _looks_like_price)
    mat_col = _infer_column(grid, _looks_like_future_date)
    coupon_col = _infer_column(grid, _looks_like_coupon)

    out: list[ScreenerBond] = []
    for tds, cells in zip(trs, grid):
        m = _ISIN_RE.search(" ".join(cells).upper())
        isin = m.group(1) if m else None

        name = _clean(cells[name_col]) if name_col < len(cells) else ""
        if not name and tds:
            link = tds[0].find("a")
            name = _clean(link.get_text()) if link else (isin or "")
        if not name or (require_isin and not isin):
            continue

        price = _num(cells[price_col], max_plausible=10_000) if price_col is not None and price_col < len(cells) else None
        maturity = _parse_date(cells[mat_col]) if mat_col is not None and mat_col < len(cells) else None
        coupon = _num(cells[coupon_col], max_plausible=100) if coupon_col is not None and coupon_col < len(cells) else None

        maturity = maturity or maturity_from_name(name)
        coupon = coupon if coupon is not None else coupon_from_name(name)

        out.append(ScreenerBond(
            isin=isin or _synth_id(name, country), name=name, market=market,
            country=country, type=bond_type, price=price, coupon=coupon,
            maturity=maturity, currency="EUR",
        ))
    return out


# ---------------------------------------------------------------------------
# CSV import (paste / upload) — the manual fallback
# ---------------------------------------------------------------------------
_CSV_ALIASES = {
    "isin": ("isin", "id_isin", "idisin", "codice", "code"),
    "name": ("name", "nome", "security", "securitydes", "description",
             "descrizione", "des", "titolo", "strumento"),
    "price": ("price", "prezzo", "pxlast", "px_last", "last", "cleanprice", "quotazione"),
    "coupon": ("coupon", "cpn", "cedola", "tasso"),
    "maturity": ("maturity", "maturitydate", "scadenza", "matdate", "expiry"),
    "currency": ("currency", "crncy", "ccy", "valuta"),
}


def _fold(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").strip().lower())


def _resolve_columns(headers: list[str]) -> dict[str, int]:
    folded = [_fold(h) for h in headers]
    idx: dict[str, int] = {}
    for field, aliases in _CSV_ALIASES.items():
        for a in aliases:
            if a in folded:
                idx[field] = folded.index(a)
                break
    return idx


def _sniff_delimiter(sample: str) -> str:
    """Delimiter by consistency across non-empty lines, not csv.Sniffer.

    Sniffer misfires on exports that open with title rows of empty ``;;;``
    cells, collapsing every ``;`` line into one cell. Counting how evenly
    each candidate splits the lines is more reliable.
    """
    lines = [ln for ln in sample.splitlines() if ln.strip()][:15]
    best, best_score = ",", -1.0
    for delim in (",", ";", "\t", "|"):
        counts = [ln.count(delim) for ln in lines]
        if max(counts, default=0) == 0:
            continue
        mode = max(set(counts), key=counts.count)
        score = mode * (sum(1 for c in counts if c == mode) / len(counts))
        if score > best_score:
            best, best_score = delim, score
    return best


def _dominant_date_order(values: list[str]) -> str:
    """Infer 'us' (MM/DD) or 'eu' (DD/MM) from a column of dates.

    A single 07/01/34 is ambiguous, but a column usually holds one that is
    not: 10/30/31 has 30 in the second slot, so the column is MM/DD.
    """
    us = eu = 0
    for v in values:
        mo = re.match(r"\s*(\d{1,2})[/\-.](\d{1,2})[/\-.]\d{2,4}\s*$", v or "")
        if not mo:
            continue
        a, b = int(mo.group(1)), int(mo.group(2))
        if b > 12 and a <= 12:
            us += 1
        elif a > 12 and b <= 12:
            eu += 1
    return "us" if us > eu else "eu" if eu > us else ""


def _parse_date_ordered(txt: str, order: str) -> date | None:
    txt = _clean(txt)
    if order == "us":
        mo = re.match(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", txt)
        if mo:
            m, d, y = (int(g) for g in mo.groups())
            y += (2000 if y < 70 else 1900) if y < 100 else 0
            try:
                return date(y, m, d)
            except ValueError:
                return None
    return _parse_date(txt)


def parse_bond_csv(data: str | bytes, *, bond_type: str = "govt",
                   default_date_order: str = "eu") -> list[ScreenerBond]:
    """Parse a pasted/uploaded bond CSV into screener rows.

    Tolerant of column order, header naming (English or Italian), both
    number and date locales, leading title rows, and comma/semicolon/tab
    delimiters. A row needs at least a name or an ISIN. `default_date_order`
    is the tie-breaker when the maturity column cannot disambiguate itself.
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")
    reader = list(csv.reader(io.StringIO(data), delimiter=_sniff_delimiter(data[:8192])))
    if not reader:
        return []

    header_row, cols = 0, {}
    for i, row in enumerate(reader[:10]):
        c = _resolve_columns(row)
        if ("name" in c or "isin" in c) and len(c) >= 2:
            header_row, cols = i, c
            break
    if not cols:
        cols, header_row = _resolve_columns(reader[0]), 0
    if "name" not in cols and "isin" not in cols:
        raise ValueError(
            "CSV not recognised: at least a name/description or ISIN column "
            "is required. Headers seen: " + ", ".join(reader[header_row][:8])
        )

    def cell(row, field):
        i = cols.get(field)
        return row[i].strip() if i is not None and i < len(row) else ""

    body = reader[header_row + 1:]
    order = _dominant_date_order([cell(r, "maturity") for r in body]) or default_date_order

    out: list[ScreenerBond] = []
    for row in body:
        if not any(c.strip() for c in row):
            continue
        name = cell(row, "name")
        m = _ISIN_RE.search(cell(row, "isin").upper())
        isin = m.group(1) if m else None
        if not name and not isin:
            continue
        maturity = _parse_date_ordered(cell(row, "maturity"), order) or maturity_from_name(name)
        coupon = _num(cell(row, "coupon"), max_plausible=100)
        if coupon is None:
            coupon = coupon_from_name(name)
        out.append(ScreenerBond(
            isin=isin or _synth_id(name or "CSV"),
            name=_clean(name or isin or ""), market="csv",
            country=(isin or "IT")[:2] if isin else "IT", type=bond_type,
            price=_num(cell(row, "price"), max_plausible=10_000),
            coupon=coupon, maturity=maturity,
            currency=cell(row, "currency") or "EUR",
        ))
    return out


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
class BorsaItalianaProvider:
    key = "borsa"
    label = "Borsa Italiana (MOT/EuroMOT)"

    def available(self) -> bool:
        return _requests is not None and _BS is not None

    def fetch(self, markets: list[str] | None = None) -> list[ScreenerBond]:
        from algohns.modules.bond_data import MOT_LISTS

        out: list[ScreenerBond] = []
        for m in (markets or list(MOT_LISTS.keys())):
            try:
                out.extend(fetch_mot_list(m))
            except Exception as exc:  # noqa: BLE001
                log.info("Borsa Italiana %s failed: %s", m, exc)
        if not out:
            raise RuntimeError("no rows from Borsa Italiana")
        return out


class RendimentiBtpProvider:
    """Scrapes every BTP from rendimentibtp.it.

    Not validated against the live page from this build (the proxy blocks the
    site, as it blocks Borsa Italiana), so the parser is resilient rather
    than pinned to one layout, and `CsvImportProvider` is the manual fallback.
    """

    key = "rendimentibtp"
    label = "rendimentibtp.it — all BTPs"

    def __init__(self, url: str = RENDIMENTIBTP_URL, timeout: int = 25) -> None:
        self.url, self.timeout = url, timeout

    def available(self) -> bool:
        return _requests is not None and _BS is not None

    def fetch(self, markets: list[str] | None = None) -> list[ScreenerBond]:
        if not self.available():
            raise RuntimeError("requests/beautifulsoup4 not installed")
        resp = _requests.get(
            self.url, headers={"User-Agent": "Mozilla/5.0 (Algohns bond screener)"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        bonds = parse_bond_table(resp.text, market="rendimentibtp", country="IT",
                                 bond_type="govt")
        if not bonds:
            raise RuntimeError("rendimentibtp: no BTP rows parsed")
        return bonds


class CsvImportProvider:
    key = "csv"
    label = "Import CSV (paste/upload)"

    def __init__(self, payload: str | bytes | None = None) -> None:
        self.payload = payload

    def available(self) -> bool:
        return self.payload is not None

    def fetch(self, markets: list[str] | None = None) -> list[ScreenerBond]:
        if self.payload is None:
            raise RuntimeError("No CSV provided.")
        bonds = parse_bond_csv(self.payload)
        if not bonds:
            raise RuntimeError("No valid rows in the CSV.")
        return bonds


class LSEGBundledProvider:
    """Real Italian sovereigns from a committed LSEG snapshot.

    The LSEG connector serves the chat session, not the deployed app, so the
    real data ships as a point-in-time snapshot (``scripts/build_lseg_dataset.py``)
    rather than a live call. It carries LSEG bid yield / modified duration /
    G-spread as cross-check columns beside the platform's ICMA engine.
    Personal / educational use only — LSEG data carries redistribution terms.
    """

    key = "lseg"
    label = "LSEG — Italian sovereign comparables (bundled)"

    def available(self) -> bool:
        return bool(load_lseg_bundled())

    def fetch(self, markets: list[str] | None = None) -> list[ScreenerBond]:
        bonds = load_lseg_bundled()
        if not bonds:
            raise RuntimeError("LSEG snapshot not bundled")
        return bonds


class SampleProvider:
    key = "sample"
    label = "Sample universe (offline)"

    def available(self) -> bool:
        return True

    def fetch(self, markets: list[str] | None = None) -> list[ScreenerBond]:
        return load_sample()


def get_provider(key: str, **kwargs) -> BondProvider:
    registry = {
        "lseg": LSEGBundledProvider,
        "borsa": BorsaItalianaProvider,
        "rendimentibtp": RendimentiBtpProvider,
        "csv": CsvImportProvider,
        "sample": SampleProvider,
    }
    if key not in registry:
        raise KeyError(f"unknown provider: {key!r}")
    return registry[key](**kwargs) if kwargs else registry[key]()


def load_universe(
    source: str = "rendimentibtp",
    markets: list[str] | None = None,
    csv_payload: str | bytes | None = None,
) -> tuple[list[ScreenerBond], str]:
    """Load the universe from `source`, falling back to the sample set.

    Returns (bonds, status) where status is ``live:<source>`` or ``sample``,
    so the page can be honest about where the numbers came from.
    """
    kwargs = {"payload": csv_payload} if source == "csv" else {}
    try:
        prov = get_provider(source, **kwargs)
        if prov.available():
            bonds = prov.fetch(markets)
            if bonds:
                return bonds, f"live:{source}"
    except Exception as exc:  # noqa: BLE001 - any failure falls back
        log.info("provider %s failed: %s", source, exc)
    return load_sample(), "sample"


SOURCE_LABELS = {
    "lseg": "LSEG — Italian sovereign comparables (bundled)",
    "rendimentibtp": "rendimentibtp.it — all BTPs",
    "borsa": "Borsa Italiana (MOT/EuroMOT)",
    "csv": "Import CSV (paste/upload)",
    "sample": "Sample universe (offline)",
}
