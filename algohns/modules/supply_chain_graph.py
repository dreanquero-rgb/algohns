"""Module 4 — S&P 500 Supply Chain Graph Analytics.

Pipeline
--------
1. **Ingest** 10-K / 10-Q filings via ``sec-edgar-downloader`` (falls back to
   the public EDGAR full-text search + document API when the package is absent).
2. **Extract** supplier / customer relationships using a hybrid of spaCy NER
   (ORG entities) and curated RegEx cue phrases ("our largest customer",
   "we rely on ... as a supplier", "concentration of ...").
3. **Graph** the relationships as a directed graph with ``networkx`` and render
   an interactive HTML view with ``PyVis``.
4. **Analyse** systemic risk: centrality, contagion reachability and the most
   systemically important nodes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings
from ..core.utils import is_available, lazy_import, require

_edgar_dl = lazy_import(
    "sec_edgar_downloader", pip_name="sec-edgar-downloader", reason="download SEC filings"
)
_spacy = lazy_import("spacy", pip_name="spacy", reason="run entity recognition")
_nx = lazy_import("networkx", pip_name="networkx", reason="build the supply-chain graph")
_pyvis = lazy_import("pyvis.network", pip_name="pyvis", reason="render the interactive graph")
_requests = lazy_import("requests", pip_name="requests", reason="query SEC EDGAR")


# Cue phrases that typically flag a supply-chain relationship in MD&A / risk
# sections of 10-K/10-Q filings. (relation, regex) — relation is graph edge type.
_CUE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("customer", re.compile(r"(?:largest|significant|major|key|principal)\s+customers?\s+(?:include|are|is)?\s*([^.]{3,160})", re.I)),
    ("customer", re.compile(r"customers?\s+such\s+as\s+([^.]{3,160})", re.I)),
    ("supplier", re.compile(r"(?:rely|depend)s?\s+on\s+([^.]{3,160}?)\s+(?:as\s+(?:a|our)\s+)?(?:supplier|vendor|manufacturer)", re.I)),
    ("supplier", re.compile(r"(?:suppliers?|vendors?)\s+(?:include|such\s+as)\s+([^.]{3,160})", re.I)),
    ("partner", re.compile(r"strategic\s+(?:partners?|alliances?)\s+(?:with|include)\s+([^.]{3,160})", re.I)),
]

# Legal-entity suffixes used to prune spurious ORG spans.
_ORG_SUFFIX = re.compile(
    r"\b(Inc|Incorporated|Corp|Corporation|Company|Co|Ltd|LLC|PLC|Group|Holdings|Technologies|Systems|Motors|Semiconductor)\b",
    re.I,
)


@dataclass
class Relationship:
    source: str
    target: str
    relation: str  # customer | supplier | partner
    evidence: str = ""


@dataclass
class SupplyChainResult:
    company: str
    relationships: list[Relationship] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def edges(self) -> list[tuple[str, str, str]]:
        return [(r.source, r.target, r.relation) for r in self.relationships]


class SupplyChainAnalyzer:
    """Mine filings and build the supply-chain graph."""

    def __init__(self, download_dir: Path | None = None) -> None:
        settings = get_settings()
        self.download_dir = download_dir or (settings.data_dir / "edgar")
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = settings.sec_user_agent
        self._nlp = None  # lazy spaCy pipeline

    # ---------------------------------------------------------------- ingest
    def fetch_filing_text(self, ticker: str, form: str = "10-K", limit: int = 1) -> str:
        """Return concatenated filing text for a ticker.

        Prefers ``sec-edgar-downloader``; falls back to EDGAR's REST API.
        """
        if is_available(_edgar_dl):
            try:
                return self._fetch_via_downloader(ticker, form, limit)
            except Exception:  # noqa: BLE001 - fall through to REST
                pass
        return self._fetch_via_rest(ticker, form, limit)

    def _fetch_via_downloader(self, ticker: str, form: str, limit: int) -> str:
        dl = _edgar_dl.Downloader(
            self.user_agent.split()[0] or "Algohns",
            self.user_agent.split()[-1] if "@" in self.user_agent else "contact@example.com",
            str(self.download_dir),
        )
        dl.get(form, ticker, limit=limit, download_details=True)
        text_parts: list[str] = []
        base = self.download_dir / "sec-edgar-filings" / ticker / form
        for path in base.rglob("*.txt"):
            try:
                text_parts.append(path.read_text(errors="ignore"))
            except Exception:  # noqa: BLE001
                continue
        return _strip_html("\n".join(text_parts))

    def _fetch_via_rest(self, ticker: str, form: str, limit: int) -> str:
        requests = require(_requests)
        headers = {"User-Agent": self.user_agent}
        # 1. ticker -> CIK
        tmap = requests.get(
            "https://www.sec.gov/files/company_tickers.json", headers=headers, timeout=30
        ).json()
        cik = None
        for row in tmap.values():
            if row["ticker"].upper() == ticker.upper():
                cik = str(row["cik_str"]).zfill(10)
                break
        if cik is None:
            raise ValueError(f"ticker {ticker} not found in EDGAR")
        # 2. recent filings
        subs = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json", headers=headers, timeout=30
        ).json()
        recent = subs["filings"]["recent"]
        docs: list[str] = []
        count = 0
        for i, ftype in enumerate(recent["form"]):
            if ftype != form:
                continue
            acc = recent["accessionNumber"][i].replace("-", "")
            doc = recent["primaryDocument"][i]
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{doc}"
            try:
                docs.append(requests.get(url, headers=headers, timeout=60).text)
                count += 1
            except Exception:  # noqa: BLE001
                continue
            if count >= limit:
                break
        return _strip_html("\n".join(docs))

    # --------------------------------------------------------------- extract
    def _pipeline(self):
        if self._nlp is not None:
            return self._nlp
        if is_available(_spacy):
            try:
                self._nlp = _spacy.load("en_core_web_sm")
            except Exception:  # noqa: BLE001 - model not downloaded
                try:
                    self._nlp = _spacy.blank("en")
                except Exception:  # noqa: BLE001
                    self._nlp = None
        return self._nlp

    def extract_relationships(self, company: str, text: str) -> list[Relationship]:
        """Extract relationships from filing text via RegEx cues + spaCy NER."""
        rels: list[Relationship] = []
        seen: set[tuple[str, str, str]] = set()
        nlp = self._pipeline()

        for relation, pattern in _CUE_PATTERNS:
            for match in pattern.finditer(text):
                span = match.group(1)
                for org in self._orgs_in_span(span, nlp):
                    key = (company, org, relation)
                    if org.lower() == company.lower() or key in seen:
                        continue
                    seen.add(key)
                    rels.append(
                        Relationship(
                            source=company,
                            target=org,
                            relation=relation,
                            evidence=match.group(0)[:200].strip(),
                        )
                    )
        return rels

    def _orgs_in_span(self, span: str, nlp) -> list[str]:
        orgs: list[str] = []
        if nlp is not None and nlp.has_pipe("ner"):
            for ent in nlp(span).ents:
                if ent.label_ == "ORG":
                    orgs.append(_normalize_org(ent.text))
        # RegEx fallback / augmentation: capitalised runs ending in a suffix.
        for m in re.finditer(r"([A-Z][\w&.\-]+(?:\s+[A-Z][\w&.\-]+){0,4})", span):
            cand = m.group(1)
            if _ORG_SUFFIX.search(cand):
                orgs.append(_normalize_org(cand))
        # de-dupe preserving order
        out, s = [], set()
        for o in orgs:
            if o and o.lower() not in s and len(o) > 2:
                s.add(o.lower())
                out.append(o)
        return out

    # ----------------------------------------------------------------- graph
    def build_graph(self, results: list[SupplyChainResult]):
        nx = require(_nx)
        g = nx.DiGraph()
        for res in results:
            g.add_node(res.company, kind="focal")
            for rel in res.relationships:
                g.add_node(rel.target, kind="counterparty")
                # Direction encodes flow of goods: supplier -> company -> customer.
                if rel.relation == "supplier":
                    g.add_edge(rel.target, res.company, relation="supplies")
                elif rel.relation == "customer":
                    g.add_edge(res.company, rel.target, relation="sells_to")
                else:
                    g.add_edge(res.company, rel.target, relation=rel.relation)
        return g

    def systemic_metrics(self, g) -> dict:
        """Centrality + contagion metrics identifying systemic nodes."""
        nx = require(_nx)
        if g.number_of_nodes() == 0:
            return {}
        deg = dict(g.degree())
        try:
            pagerank = nx.pagerank(g)
        except Exception:  # noqa: BLE001
            pagerank = {n: 0.0 for n in g.nodes}
        betweenness = nx.betweenness_centrality(g) if g.number_of_nodes() < 500 else {}
        # Contagion: reachable set size from each node (downstream exposure).
        contagion = {n: len(nx.descendants(g, n)) for n in g.nodes}
        top = sorted(pagerank.items(), key=lambda kv: kv[1], reverse=True)[:10]
        return {
            "nodes": g.number_of_nodes(),
            "edges": g.number_of_edges(),
            "density": round(nx.density(g), 4),
            "top_systemic": [{"node": n, "pagerank": round(v, 4), "degree": deg.get(n, 0),
                              "contagion_reach": contagion.get(n, 0)} for n, v in top],
            "pagerank": pagerank,
            "betweenness": betweenness,
            "contagion": contagion,
        }

    def render_pyvis(self, g, output_path: str | Path) -> str:
        """Write an interactive PyVis HTML file and return its path.

        High-contrast rendering: edges are coloured by relation type (bright on
        a dark-but-not-black canvas), nodes are sized by degree, and directed
        arrows show the flow of goods.
        """
        pyvis = require(_pyvis)
        nx = require(_nx)
        net = pyvis.Network(
            height="720px", width="100%", directed=True,
            bgcolor="#0B1220", font_color="#F8FAFC",
        )
        net.barnes_hut(gravity=-9000, spring_length=140, spring_strength=0.02)

        degrees = dict(g.degree())
        max_deg = max(degrees.values()) if degrees else 1
        for node, data in g.nodes(data=True):
            focal = data.get("kind") == "focal"
            deg = degrees.get(node, 1)
            size = 30 if focal else 14 + 16 * (deg / max_deg)
            net.add_node(
                node, label=node,
                color={
                    "background": "#E2B86B" if focal else "#38BDF8",
                    "border": "#F8FAFC" if focal else "#0EA5E9",
                    "highlight": {"background": "#FCD34D", "border": "#fff"},
                },
                size=size, borderWidth=2,
                title=f"{node} — degree {deg}",
                font={"size": 18, "color": "#F8FAFC", "strokeWidth": 3, "strokeColor": "#0B1220"},
            )
        # Bright, distinct edge colours per relation type (the old grey #334155
        # was invisible on black — this is the fix).
        edge_colors = {"supplies": "#34D399", "sells_to": "#E2B86B", "partner": "#38BDF8"}
        for u, v, data in g.edges(data=True):
            rel = data.get("relation", "")
            net.add_edge(
                u, v, title=rel, label=rel,
                color=edge_colors.get(rel, "#94A3B8"),
                width=2.5, arrowStrikethrough=False,
                font={"size": 11, "color": "#CBD5E1", "strokeWidth": 2, "strokeColor": "#0B1220", "align": "middle"},
            )
        net.set_edge_smooth("dynamic")
        output_path = str(output_path)
        try:
            net.write_html(output_path, notebook=False)
        except Exception:  # noqa: BLE001 - older pyvis API
            net.save_graph(output_path)
        return output_path

    # Colour legend the UI can render next to the graph.
    EDGE_LEGEND = {
        "supplies (supplier → company)": "#34D399",
        "sells_to (company → customer)": "#E2B86B",
        "partner": "#38BDF8",
    }

    # ------------------------------------------------------------- one-shot
    def analyse(self, ticker: str, form: str = "10-K", limit: int = 1) -> SupplyChainResult:
        text = self.fetch_filing_text(ticker, form=form, limit=limit)
        rels = self.extract_relationships(ticker, text)
        return SupplyChainResult(company=ticker, relationships=rels,
                                 metrics={"chars_analysed": len(text), "relationships": len(rels)})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def sample_results() -> list[SupplyChainResult]:
    """Illustrative real-world S&P 500 supply-chain links (offline fallback).

    Used when live SEC filing access is unavailable (e.g. this sandbox blocks
    EDGAR). On deploy the live 10-K/10-Q miner replaces this.
    """
    data = {
        "AAPL": [("Taiwan Semiconductor", "supplier"), ("Foxconn", "supplier"),
                 ("Broadcom", "supplier"), ("Corning", "supplier"), ("Verizon", "customer")],
        "NVDA": [("Taiwan Semiconductor", "supplier"), ("SK Hynix", "supplier"),
                 ("Microsoft", "customer"), ("Meta Platforms", "customer"), ("Amazon", "customer")],
        "TSLA": [("Panasonic", "supplier"), ("CATL", "supplier"), ("Nvidia", "supplier")],
        "MSFT": [("Nvidia", "supplier"), ("AMD", "supplier"), ("Intel", "supplier")],
        "AMZN": [("Nvidia", "supplier"), ("Intel", "supplier")],
    }
    out = []
    for company, rels in data.items():
        out.append(SupplyChainResult(
            company=company,
            relationships=[Relationship(company, t, r, evidence="sample dataset") for t, r in rels],
            metrics={"relationships": len(rels), "source": "sample"},
        ))
    return out


def _strip_html(raw: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&#\d+;|&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_org(name: str) -> str:
    name = re.sub(r"[\"'`]", "", name).strip(" ,.;:-")
    name = re.sub(r"\s+", " ", name)
    return name.strip()
