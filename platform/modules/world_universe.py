"""Curated world dataset for the World Simulation.

**Provenance, stated plainly:** this is a hand-curated dataset, not a scrape.
Company domiciles, operating locations and the broad shape of the revenue
geography are drawn from public knowledge; the supply-chain edges are marked
`OBSERVED_FILING` only where the relationship is publicly disclosed and
widely reported (ASML->TSMC lithography, TSMC->Apple/NVIDIA foundry), and
`INFERRED_IO` everywhere else.

The exact dependence percentages are **modelling assumptions**, not
disclosed figures. They are plausible and internally consistent, which is
enough to exercise propagation mechanics, and nowhere near precise enough to
trade on. Module 5's segment extraction is what replaces the revenue
geography with filed numbers; module 4's filing extraction is what replaces
these edges with cited ones.

Coordinates are real operating-centre locations (lat, lon), because the
globe places companies where they actually make things — putting Apple at
Washington DC because it is "US" would defeat the point.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["COUNTRIES", "COMPANIES", "SUPPLY_LINKS", "CompanySpec", "CountrySpec"]


@dataclass(frozen=True)
class CountrySpec:
    code: str
    name: str
    lat: float
    lon: float
    region: str


@dataclass(frozen=True)
class CompanySpec:
    ticker: str
    name: str
    sector: str
    domicile: str
    # Operating centre, which is where the globe draws it.
    lat: float
    lon: float
    # Share of revenue by country/region. Must sum to ~1.
    revenue_geography: dict[str, float]
    market_cap_usd: float
    inventory_days: float
    substitutability: float
    beta: float


COUNTRIES: dict[str, CountrySpec] = {
    c.code: c for c in [
        CountrySpec("US", "United States", 38.90, -77.04, "Americas"),
        CountrySpec("CA", "Canada", 43.65, -79.38, "Americas"),
        CountrySpec("MX", "Mexico", 19.43, -99.13, "Americas"),
        CountrySpec("BR", "Brazil", -23.55, -46.63, "Americas"),
        CountrySpec("CL", "Chile", -33.45, -70.67, "Americas"),
        CountrySpec("GB", "United Kingdom", 51.51, -0.13, "Europe"),
        CountrySpec("DE", "Germany", 52.52, 13.41, "Europe"),
        CountrySpec("FR", "France", 48.86, 2.35, "Europe"),
        CountrySpec("IT", "Italy", 45.46, 9.19, "Europe"),
        CountrySpec("ES", "Spain", 40.42, -3.70, "Europe"),
        CountrySpec("NL", "Netherlands", 52.37, 4.90, "Europe"),
        CountrySpec("CH", "Switzerland", 47.38, 8.54, "Europe"),
        CountrySpec("IE", "Ireland", 53.35, -6.26, "Europe"),
        CountrySpec("DK", "Denmark", 55.68, 12.57, "Europe"),
        CountrySpec("SE", "Sweden", 59.33, 18.07, "Europe"),
        CountrySpec("NO", "Norway", 59.91, 10.75, "Europe"),
        CountrySpec("FI", "Finland", 60.17, 24.94, "Europe"),
        CountrySpec("PL", "Poland", 52.23, 21.01, "Europe"),
        CountrySpec("TR", "Turkey", 41.01, 28.98, "EMEA"),
        CountrySpec("IL", "Israel", 32.09, 34.78, "EMEA"),
        CountrySpec("SA", "Saudi Arabia", 26.29, 50.15, "EMEA"),
        CountrySpec("AE", "UAE", 24.45, 54.38, "EMEA"),
        CountrySpec("ZA", "South Africa", -26.20, 28.04, "EMEA"),
        CountrySpec("CN", "China", 31.23, 121.47, "Asia"),
        CountrySpec("TW", "Taiwan", 24.80, 120.97, "Asia"),
        CountrySpec("JP", "Japan", 35.68, 139.69, "Asia"),
        CountrySpec("KR", "South Korea", 37.57, 126.98, "Asia"),
        CountrySpec("IN", "India", 12.97, 77.59, "Asia"),
        CountrySpec("SG", "Singapore", 1.35, 103.82, "Asia"),
        CountrySpec("VN", "Vietnam", 21.03, 105.85, "Asia"),
        CountrySpec("TH", "Thailand", 13.76, 100.50, "Asia"),
        CountrySpec("MY", "Malaysia", 3.14, 101.69, "Asia"),
        CountrySpec("ID", "Indonesia", -6.21, 106.85, "Asia"),
        CountrySpec("PH", "Philippines", 14.60, 120.98, "Asia"),
        CountrySpec("AU", "Australia", -33.87, 151.21, "Asia"),
        # Aggregate regions: several filers report "Europe" as one segment,
        # so the globe needs a coordinate for it rather than dropping the
        # revenue share on the floor.
        CountrySpec("EU", "Europe (aggregate)", 50.85, 4.35, "Europe"),
        CountrySpec("ROW", "Rest of World", 0.0, 20.0, "Other"),
    ]
}


def _c(*args, **kw) -> CompanySpec:
    return CompanySpec(*args, **kw)


COMPANIES: list[CompanySpec] = [
    # ---------------- Semiconductor chain: the tightest chokepoints -------
    _c("ASML", "ASML Holding", "Semis Equipment", "NL", 51.42, 5.40,
       {"TW": 0.38, "CN": 0.27, "KR": 0.17, "US": 0.13, "ROW": 0.05},
       3.0e11, inventory_days=0, substitutability=0.02, beta=1.45),
    _c("TSM", "Taiwan Semiconductor", "Semis", "TW", 24.80, 120.97,
       {"US": 0.68, "CN": 0.11, "TW": 0.09, "JP": 0.06, "ROW": 0.06},
       7.5e11, inventory_days=0, substitutability=0.05, beta=1.30),
    _c("005930.KS", "Samsung Electronics", "Semis", "KR", 37.24, 127.08,
       {"KR": 0.14, "US": 0.30, "CN": 0.20, "EU": 0.15, "ROW": 0.21},
       3.6e11, inventory_days=30, substitutability=0.20, beta=1.10),
    _c("NVDA", "NVIDIA", "Semis", "US", 37.35, -121.95,
       {"US": 0.47, "TW": 0.21, "CN": 0.17, "SG": 0.10, "ROW": 0.05},
       3.3e12, inventory_days=60, substitutability=0.10, beta=1.70),
    _c("AMD", "Advanced Micro Devices", "Semis", "US", 37.38, -121.98,
       {"US": 0.34, "CN": 0.24, "TW": 0.16, "JP": 0.08, "ROW": 0.18},
       2.6e11, inventory_days=50, substitutability=0.15, beta=1.65),
    _c("INTC", "Intel", "Semis", "US", 45.53, -122.94,
       {"US": 0.27, "CN": 0.27, "TW": 0.19, "SG": 0.10, "ROW": 0.17},
       1.0e11, inventory_days=90, substitutability=0.25, beta=1.25),
    _c("AVGO", "Broadcom", "Semis", "US", 37.41, -121.94,
       {"US": 0.33, "CN": 0.32, "SG": 0.12, "TW": 0.08, "ROW": 0.15},
       8.0e11, inventory_days=55, substitutability=0.18, beta=1.35),
    _c("AMAT", "Applied Materials", "Semis Equipment", "US", 37.39, -121.98,
       {"TW": 0.26, "CN": 0.29, "KR": 0.18, "US": 0.14, "ROW": 0.13},
       1.6e11, inventory_days=40, substitutability=0.12, beta=1.50),
    _c("2317.TW", "Hon Hai (Foxconn)", "Contract Mfg", "TW", 24.99, 121.46,
       {"US": 0.52, "CN": 0.22, "TW": 0.08, "ROW": 0.18},
       6.0e10, inventory_days=20, substitutability=0.22, beta=1.15),

    # ---------------- Hardware & consumer tech --------------------------
    _c("AAPL", "Apple", "Hardware", "US", 37.33, -122.03,
       {"US": 0.42, "CN": 0.19, "EU": 0.25, "JP": 0.07, "ROW": 0.07},
       3.4e12, inventory_days=35, substitutability=0.25, beta=1.20),
    _c("DELL", "Dell Technologies", "Hardware", "US", 30.51, -97.67,
       {"US": 0.62, "EU": 0.22, "CN": 0.06, "ROW": 0.10},
       8.0e10, inventory_days=25, substitutability=0.35, beta=1.10),
    _c("MSFT", "Microsoft", "Software", "US", 47.64, -122.13,
       {"US": 0.50, "EU": 0.26, "JP": 0.05, "ROW": 0.19},
       3.1e12, inventory_days=120, substitutability=0.60, beta=0.92),
    _c("GOOGL", "Alphabet", "Comm. Services", "US", 37.42, -122.08,
       {"US": 0.47, "EU": 0.29, "ROW": 0.24},
       2.2e12, inventory_days=150, substitutability=0.65, beta=1.05),
    _c("AMZN", "Amazon", "Cons. Discretionary", "US", 47.62, -122.34,
       {"US": 0.69, "DE": 0.07, "GB": 0.07, "JP": 0.05, "ROW": 0.12},
       2.0e12, inventory_days=40, substitutability=0.45, beta=1.18),

    # ---------------- Autos: long chains, thin buffers -------------------
    _c("TSLA", "Tesla", "Cons. Discretionary", "US", 30.22, -97.62,
       {"US": 0.47, "CN": 0.22, "EU": 0.19, "ROW": 0.12},
       9.0e11, inventory_days=18, substitutability=0.20, beta=2.05),
    _c("TM", "Toyota Motor", "Cons. Discretionary", "JP", 35.08, 137.16,
       {"JP": 0.25, "US": 0.32, "EU": 0.12, "CN": 0.10, "ROW": 0.21},
       2.6e11, inventory_days=15, substitutability=0.25, beta=0.85),
    _c("VOW3.DE", "Volkswagen", "Cons. Discretionary", "DE", 52.42, 10.79,
       {"EU": 0.42, "CN": 0.30, "US": 0.14, "ROW": 0.14},
       6.0e10, inventory_days=16, substitutability=0.28, beta=1.25),
    _c("BOSCH", "Robert Bosch", "Auto Components", "DE", 48.78, 9.18,
       {"EU": 0.48, "CN": 0.18, "US": 0.18, "ROW": 0.16},
       5.0e10, inventory_days=22, substitutability=0.20, beta=1.10),
    _c("MBG.DE", "Mercedes-Benz", "Cons. Discretionary", "DE", 48.78, 9.23,
       {"EU": 0.40, "CN": 0.24, "US": 0.22, "ROW": 0.14},
       6.5e10, inventory_days=17, substitutability=0.30, beta=1.20),

    # ---------------- Energy: the shock transmitter ----------------------
    _c("2222.SR", "Saudi Aramco", "Energy", "SA", 26.29, 50.15,
       {"CN": 0.25, "JP": 0.14, "IN": 0.12, "KR": 0.11, "ROW": 0.38},
       1.8e12, inventory_days=10, substitutability=0.35, beta=0.75),
    _c("XOM", "Exxon Mobil", "Energy", "US", 32.92, -96.97,
       {"US": 0.51, "EU": 0.17, "ROW": 0.32},
       5.0e11, inventory_days=12, substitutability=0.40, beta=0.88),
    _c("SHEL", "Shell", "Energy", "GB", 51.51, -0.13,
       {"EU": 0.38, "US": 0.22, "ROW": 0.40},
       2.2e11, inventory_days=12, substitutability=0.40, beta=0.90),
    _c("EQNR", "Equinor", "Energy", "NO", 58.97, 5.73,
       {"EU": 0.72, "US": 0.14, "ROW": 0.14},
       7.0e10, inventory_days=11, substitutability=0.42, beta=0.95),

    # ---------------- Materials & mining: upstream of everything ---------
    _c("BHP", "BHP Group", "Materials", "AU", -31.95, 115.86,
       {"CN": 0.62, "JP": 0.13, "KR": 0.07, "ROW": 0.18},
       1.4e11, inventory_days=25, substitutability=0.30, beta=1.05),
    _c("RIO", "Rio Tinto", "Materials", "GB", 51.50, -0.14,
       {"CN": 0.57, "US": 0.13, "JP": 0.11, "ROW": 0.19},
       1.1e11, inventory_days=26, substitutability=0.32, beta=1.02),
    _c("SQM", "Sociedad Química y Minera", "Materials", "CL", -23.65, -70.40,
       {"CN": 0.45, "KR": 0.16, "US": 0.12, "EU": 0.12, "ROW": 0.15},
       1.2e10, inventory_days=30, substitutability=0.25, beta=1.30),
    _c("LIN", "Linde", "Materials", "GB", 51.51, -0.12,
       {"US": 0.42, "EU": 0.30, "CN": 0.12, "ROW": 0.16},
       2.3e11, inventory_days=8, substitutability=0.18, beta=0.95),

    # ---------------- Industrials & logistics ---------------------------
    _c("CAT", "Caterpillar", "Industrials", "US", 40.69, -89.59,
       {"US": 0.47, "EU": 0.17, "CN": 0.08, "ROW": 0.28},
       1.7e11, inventory_days=70, substitutability=0.30, beta=1.25),
    _c("MAERSK", "A.P. Moller-Maersk", "Industrials", "DK", 55.68, 12.59,
       {"EU": 0.35, "US": 0.24, "CN": 0.20, "ROW": 0.21},
       3.0e10, inventory_days=5, substitutability=0.25, beta=1.20),
    _c("SIE.DE", "Siemens", "Industrials", "DE", 48.14, 11.58,
       {"EU": 0.45, "US": 0.24, "CN": 0.13, "ROW": 0.18},
       1.6e11, inventory_days=55, substitutability=0.28, beta=1.10),
    _c("AIR.PA", "Airbus", "Industrials", "FR", 43.63, 1.37,
       {"EU": 0.38, "US": 0.20, "CN": 0.14, "ROW": 0.28},
       1.4e11, inventory_days=85, substitutability=0.12, beta=1.15),

    # ---------------- Health care ---------------------------------------
    _c("JNJ", "Johnson & Johnson", "Health Care", "US", 40.50, -74.45,
       {"US": 0.56, "EU": 0.22, "ROW": 0.22},
       3.8e11, inventory_days=95, substitutability=0.45, beta=0.62),
    _c("NOVN.SW", "Novartis", "Health Care", "CH", 47.56, 7.59,
       {"US": 0.37, "EU": 0.29, "ROW": 0.34},
       2.2e11, inventory_days=100, substitutability=0.48, beta=0.60),
    _c("LLY", "Eli Lilly", "Health Care", "US", 39.77, -86.16,
       {"US": 0.64, "EU": 0.16, "ROW": 0.20},
       7.0e11, inventory_days=90, substitutability=0.40, beta=0.55),

    # ---------------- Consumer staples ----------------------------------
    _c("NESN.SW", "Nestlé", "Cons. Staples", "CH", 46.46, 6.84,
       {"EU": 0.31, "US": 0.31, "BR": 0.07, "ROW": 0.31},
       2.6e11, inventory_days=60, substitutability=0.50, beta=0.50),
    _c("KO", "Coca-Cola", "Cons. Staples", "US", 33.76, -84.39,
       {"US": 0.38, "EU": 0.17, "MX": 0.07, "ROW": 0.38},
       2.7e11, inventory_days=55, substitutability=0.52, beta=0.58),
    _c("PG", "Procter & Gamble", "Cons. Staples", "US", 39.10, -84.51,
       {"US": 0.46, "EU": 0.22, "CN": 0.09, "ROW": 0.23},
       3.8e11, inventory_days=58, substitutability=0.50, beta=0.45),

    # ---------------- Financials: shock receivers, not transmitters ------
    _c("JPM", "JPMorgan Chase", "Financials", "US", 40.76, -73.98,
       {"US": 0.75, "EU": 0.13, "ROW": 0.12},
       6.5e11, inventory_days=365, substitutability=0.90, beta=1.12),
    _c("HSBA.L", "HSBC Holdings", "Financials", "GB", 51.51, -0.02,
       {"CN": 0.31, "GB": 0.28, "ROW": 0.41},
       1.7e11, inventory_days=365, substitutability=0.90, beta=1.00),
    _c("ISP.MI", "Intesa Sanpaolo", "Financials", "IT", 45.07, 7.68,
       {"IT": 0.78, "EU": 0.16, "ROW": 0.06},
       7.0e10, inventory_days=365, substitutability=0.92, beta=1.15),
    _c("ALV.DE", "Allianz", "Financials", "DE", 48.15, 11.58,
       {"EU": 0.62, "US": 0.22, "ROW": 0.16},
       1.2e11, inventory_days=365, substitutability=0.90, beta=0.95),

    # ---------------- Utilities & real assets ---------------------------
    _c("NEE", "NextEra Energy", "Utilities", "US", 26.71, -80.06,
       {"US": 1.00}, 1.5e11, inventory_days=300, substitutability=0.85, beta=0.60),
    _c("ENEL.MI", "Enel", "Utilities", "IT", 41.90, 12.50,
       {"IT": 0.44, "ES": 0.19, "BR": 0.13, "ROW": 0.24},
       8.0e10, inventory_days=300, substitutability=0.82, beta=0.70),
# ---------------- Remaining S&P 500 backtest universe ----------------
    # These complete the overlap with the optimiser's default universe, so
    # the globe shows every name the backtest can hold.
    _c("META", "Meta Platforms", "Comm. Services", "US", 37.48, -122.15,
       {"US": 0.42, "EU": 0.23, "ROW": 0.35},
       1.5e12, inventory_days=140, substitutability=0.62, beta=1.35),
    _c("DIS", "Walt Disney", "Comm. Services", "US", 34.18, -118.32,
       {"US": 0.74, "EU": 0.13, "ROW": 0.13},
       2.0e11, inventory_days=120, substitutability=0.55, beta=1.25),
    _c("HD", "Home Depot", "Cons. Discretionary", "US", 33.76, -84.39,
       {"US": 0.93, "CA": 0.05, "MX": 0.02},
       3.9e11, inventory_days=65, substitutability=0.42, beta=1.00),
    _c("WMT", "Walmart", "Cons. Staples", "US", 36.37, -94.21,
       {"US": 0.69, "MX": 0.08, "CN": 0.04, "ROW": 0.19},
       7.0e11, inventory_days=42, substitutability=0.40, beta=0.62),
    _c("UNH", "UnitedHealth Group", "Health Care", "US", 44.94, -93.46,
       {"US": 0.98, "BR": 0.02},
       4.8e11, inventory_days=300, substitutability=0.80, beta=0.68),
    _c("BAC", "Bank of America", "Financials", "US", 35.23, -80.84,
       {"US": 0.84, "EU": 0.09, "ROW": 0.07},
       3.5e11, inventory_days=365, substitutability=0.90, beta=1.25),
    _c("BRK-B", "Berkshire Hathaway", "Financials", "US", 41.26, -95.93,
       {"US": 0.88, "EU": 0.06, "ROW": 0.06},
       1.0e12, inventory_days=365, substitutability=0.85, beta=0.85),
    _c("CVX", "Chevron", "Energy", "US", 29.76, -95.37,
       {"US": 0.58, "EU": 0.13, "ROW": 0.29},
       3.0e11, inventory_days=12, substitutability=0.40, beta=0.92),
    _c("GE", "GE Aerospace", "Industrials", "US", 39.27, -84.47,
       {"US": 0.50, "EU": 0.21, "ROW": 0.29},
       2.0e11, inventory_days=80, substitutability=0.15, beta=1.30),
    _c("UNP", "Union Pacific", "Industrials", "US", 41.26, -95.93,
       {"US": 1.00},
       1.4e11, inventory_days=30, substitutability=0.35, beta=1.05),
    _c("AMT", "American Tower", "Real Estate", "US", 42.36, -71.06,
       {"US": 0.72, "IN": 0.10, "BR": 0.08, "ROW": 0.10},
       9.0e10, inventory_days=300, substitutability=0.75, beta=0.80),
]

# (source, target, dependence, provenance_is_observed)
# `dependence` is the share of the *target's* critical inputs that the source
# supplies. Observed = the relationship is publicly disclosed and widely
# reported; the percentage is still a modelling assumption.
SUPPLY_LINKS: list[tuple[str, str, float, bool]] = [
    # EUV lithography: the tightest single chokepoint in the world economy.
    ("ASML", "TSM", 0.95, True),
    ("ASML", "005930.KS", 0.85, True),
    ("ASML", "INTC", 0.80, True),
    ("AMAT", "TSM", 0.45, True),
    ("AMAT", "005930.KS", 0.40, True),

    # Foundry -> fabless designers.
    ("TSM", "NVDA", 0.92, True),
    ("TSM", "AAPL", 0.80, True),
    ("TSM", "AMD", 0.88, True),
    ("TSM", "AVGO", 0.70, True),
    ("005930.KS", "AAPL", 0.30, True),

    # Silicon -> systems.
    ("NVDA", "MSFT", 0.42, True),
    ("NVDA", "GOOGL", 0.35, True),
    ("NVDA", "AMZN", 0.38, True),
    ("NVDA", "DELL", 0.55, True),
    ("AMD", "DELL", 0.30, False),
    ("INTC", "DELL", 0.35, False),
    ("AVGO", "AAPL", 0.25, True),

    # Contract manufacturing.
    ("2317.TW", "AAPL", 0.55, True),
    ("2317.TW", "DELL", 0.25, False),

    # Automotive: semis into cars, and the tier-1 supplier layer.
    ("TSM", "TSLA", 0.35, False),
    ("TSM", "BOSCH", 0.30, False),
    ("BOSCH", "VOW3.DE", 0.40, False),
    ("BOSCH", "MBG.DE", 0.38, False),
    ("BOSCH", "TM", 0.18, False),
    ("SQM", "TSLA", 0.28, False),
    ("SQM", "005930.KS", 0.20, False),

    # Energy into everything that moves or smelts.
    ("2222.SR", "MAERSK", 0.30, False),
    ("2222.SR", "AIR.PA", 0.12, False),
    ("XOM", "CAT", 0.10, False),
    ("SHEL", "MAERSK", 0.22, False),
    ("EQNR", "SIE.DE", 0.10, False),
    ("EQNR", "ENEL.MI", 0.18, False),

    # Industrial gases: small supplier, wide blast radius.
    ("LIN", "TSM", 0.25, False),
    ("LIN", "005930.KS", 0.22, False),
    ("LIN", "NOVN.SW", 0.15, False),

    # Raw materials upstream.
    ("BHP", "VOW3.DE", 0.12, False),
    ("BHP", "CAT", 0.15, False),
    ("RIO", "AIR.PA", 0.14, False),
    ("RIO", "SIE.DE", 0.10, False),

    # Logistics: a chokepoint for physical goods.
    ("MAERSK", "AAPL", 0.18, False),
    ("MAERSK", "AMZN", 0.22, False),
    ("MAERSK", "NESN.SW", 0.16, False),
    ("MAERSK", "PG", 0.15, False),
    ("MAERSK", "KO", 0.12, False),

    # Pharma inputs.
    ("LIN", "LLY", 0.12, False),
    ("NOVN.SW", "JNJ", 0.06, False),
# Newly added names wired into the chain.
    ("NVDA", "META", 0.40, True),
    ("TSM", "META", 0.20, False),
    ("MAERSK", "WMT", 0.20, False),
    ("MAERSK", "HD", 0.14, False),
    ("2317.TW", "META", 0.15, False),
    ("GE", "AIR.PA", 0.22, False),
    ("RIO", "GE", 0.10, False),
    ("UNP", "CAT", 0.12, False),
    ("UNP", "WMT", 0.10, False),
    ("XOM", "UNP", 0.14, False),
    ("CVX", "MAERSK", 0.16, False),
    ("LIN", "GE", 0.08, False),
    ("AMT", "DIS", 0.04, False),
]
