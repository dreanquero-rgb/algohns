"""Module 2 companion — Investor risk-profiling questionnaire & allocation.

Turns a short MiFID-style questionnaire into (1) a risk score, (2) a named
investor profile and (3) a suggested strategic asset allocation expressed as
tradable ETF proxies, which Module 2 can then execute / rebalance on the Alpaca
paper (demo) account and backtest.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Questionnaire definition
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Question:
    key: str
    text: str
    answers: list[tuple[str, int]]  # (label, score 0..4)


QUESTIONS: list[Question] = [
    Question("horizon", "Investment time horizon", [
        ("Less than 1 year", 0), ("1–3 years", 1), ("3–7 years", 2),
        ("7–15 years", 3), ("More than 15 years", 4)]),
    Question("goal", "Primary goal", [
        ("Preserve capital", 0), ("Income", 1), ("Balanced growth", 2),
        ("Long-term growth", 3), ("Maximise growth", 4)]),
    Question("drawdown", "If your portfolio fell 20% in a month you would", [
        ("Sell everything", 0), ("Sell part", 1), ("Do nothing", 2),
        ("Buy a little more", 3), ("Buy aggressively", 4)]),
    Question("experience", "Investing experience", [
        ("None", 0), ("Some funds/ETFs", 1), ("Stocks & ETFs", 2),
        ("Options/derivatives", 3), ("Professional", 4)]),
    Question("income_stability", "Income & savings stability", [
        ("Unstable", 0), ("Somewhat stable", 1), ("Stable", 2),
        ("Very stable", 3), ("Very high + large buffer", 4)]),
    Question("volatility_pref", "Preferred risk/return trade-off", [
        ("Lowest possible risk", 0), ("Low", 1), ("Medium", 2),
        ("High", 3), ("Highest return, high risk", 4)]),
]

# Asset-class ETF proxies (US-listed, tradable on Alpaca paper).
ASSET_PROXIES: dict[str, str] = {
    "us_equity": "SPY", "intl_equity": "VEA", "em_equity": "VWO",
    "tech": "QQQ", "bonds_agg": "AGG", "tips": "TIP",
    "short_treasury": "SHY", "gold": "GLD", "reits": "VNQ", "cash": "BIL",
}


@dataclass
class RiskProfile:
    score: float                       # 0..100
    label: str
    description: str
    allocation: dict[str, float] = field(default_factory=dict)   # class -> weight
    ticker_allocation: dict[str, float] = field(default_factory=dict)  # ETF -> weight


# Base strategic allocations per profile (weights sum to 1).
_PROFILE_ALLOCATIONS: dict[str, dict[str, float]] = {
    "Conservative": {"short_treasury": 0.35, "bonds_agg": 0.35, "tips": 0.10, "us_equity": 0.12, "gold": 0.08},
    "Moderate":     {"bonds_agg": 0.35, "short_treasury": 0.10, "us_equity": 0.28, "intl_equity": 0.12, "tips": 0.07, "gold": 0.08},
    "Balanced":     {"us_equity": 0.35, "intl_equity": 0.15, "bonds_agg": 0.28, "gold": 0.08, "reits": 0.07, "em_equity": 0.07},
    "Growth":       {"us_equity": 0.40, "intl_equity": 0.16, "em_equity": 0.10, "tech": 0.12, "bonds_agg": 0.14, "gold": 0.08},
    "Aggressive":   {"us_equity": 0.40, "tech": 0.22, "em_equity": 0.15, "intl_equity": 0.13, "gold": 0.10},
}

_PROFILE_DESC = {
    "Conservative": "Capital preservation first; low volatility, bond-heavy.",
    "Moderate": "Income with modest growth; still bond-tilted.",
    "Balanced": "Even split between growth and stability.",
    "Growth": "Growth-oriented with a diversified equity core.",
    "Aggressive": "Maximum growth; equity- and tech-heavy, high volatility.",
}


def score_to_label(score: float) -> str:
    if score < 25:
        return "Conservative"
    if score < 45:
        return "Moderate"
    if score < 65:
        return "Balanced"
    if score < 82:
        return "Growth"
    return "Aggressive"


def compute_profile(
    answers: dict[str, int],
    preferences: list[str] | None = None,
) -> RiskProfile:
    """Score the questionnaire and build the suggested allocation.

    ``answers``: {question_key: chosen_score 0..4}.
    ``preferences``: optional asset classes the investor wants to emphasise
    (keys of ASSET_PROXIES) — nudges the base allocation toward them.
    """
    max_score = 4 * len(QUESTIONS)
    raw = sum(answers.get(q.key, 0) for q in QUESTIONS)
    score = round(100 * raw / max_score, 1) if max_score else 0.0
    label = score_to_label(score)
    alloc = dict(_PROFILE_ALLOCATIONS[label])

    # Apply user preferences: add weight to preferred classes, renormalise.
    if preferences:
        boost = 0.06
        for cls in preferences:
            if cls in ASSET_PROXIES:
                alloc[cls] = alloc.get(cls, 0.0) + boost
        total = sum(alloc.values())
        alloc = {k: round(v / total, 4) for k, v in alloc.items()}

    ticker_alloc: dict[str, float] = {}
    for cls, w in alloc.items():
        ticker_alloc[ASSET_PROXIES[cls]] = round(ticker_alloc.get(ASSET_PROXIES[cls], 0.0) + w, 4)

    return RiskProfile(
        score=score, label=label, description=_PROFILE_DESC[label],
        allocation=alloc, ticker_allocation=ticker_alloc,
    )
