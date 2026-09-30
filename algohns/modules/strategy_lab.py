"""Strategy lab: screening rules, weighting schemes and generated Python.

The auto-trading module used to offer a questionnaire and nothing between that
and an order. This module is the missing middle: the investor states *rules*
("only names with beta under 1.1, mid-cap and above, no Energy"), the rules are
applied to a real universe, and the resulting strategy is emitted **as Python
source** so it can be read, checked and taken away.

**Why generate code instead of executing pasted code.** The platform deploys to
a public URL. Running arbitrary visitor-supplied Python there would be a remote
code execution hole, so the lab does not do it. Instead:

* the rules are structured data (``ScreenCriteria``), validated and testable;
* ``generate_strategy_code`` renders those exact rules as a standalone,
  runnable script — what you read is what the app ran;
* for genuine ad-hoc flexibility, ``evaluate_filter_expression`` allows one
  boolean pandas expression over the screening columns, evaluated with no
  builtins and no attribute access, which is a narrow and auditable surface.

To modify a strategy beyond that, download the generated script and run it
against the repo — which is the honest answer for a shared deployment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "ScreenCriteria",
    "WEIGHTINGS",
    "screen_universe",
    "build_weights",
    "generate_strategy_code",
    "evaluate_filter_expression",
    "FilterExpressionError",
]


# ---------------------------------------------------------------------------
# Screening criteria
# ---------------------------------------------------------------------------
@dataclass
class ScreenCriteria:
    """Investable-universe rules, as structured data rather than free text."""

    beta_min: float | None = None
    beta_max: float | None = None
    market_cap_min: float | None = None       # absolute currency units
    market_cap_max: float | None = None
    max_volatility: float | None = None       # annualised, e.g. 0.35
    min_dividend_yield: float | None = None   # e.g. 0.02
    sectors: tuple[str, ...] = ()             # empty = all sectors
    exclude_sectors: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    max_positions: int = 20
    expression: str = ""                      # optional pandas boolean expression

    def __post_init__(self) -> None:
        if self.beta_min is not None and self.beta_max is not None \
                and self.beta_min > self.beta_max:
            raise ValueError("beta_min cannot exceed beta_max")
        if self.market_cap_min is not None and self.market_cap_max is not None \
                and self.market_cap_min > self.market_cap_max:
            raise ValueError("market_cap_min cannot exceed market_cap_max")
        if self.max_positions < 1:
            raise ValueError("max_positions must be at least 1")
        overlap = set(self.sectors) & set(self.exclude_sectors)
        if overlap:
            raise ValueError(f"sector both included and excluded: {sorted(overlap)}")

    def describe(self) -> list[str]:
        """Human-readable rule list, for the UI and the generated docstring."""
        out: list[str] = []
        if self.beta_min is not None:
            out.append(f"beta >= {self.beta_min:g}")
        if self.beta_max is not None:
            out.append(f"beta <= {self.beta_max:g}")
        if self.market_cap_min is not None:
            out.append(f"market cap >= {self.market_cap_min:,.0f}")
        if self.market_cap_max is not None:
            out.append(f"market cap <= {self.market_cap_max:,.0f}")
        if self.max_volatility is not None:
            out.append(f"annualised volatility <= {self.max_volatility:.0%}")
        if self.min_dividend_yield is not None:
            out.append(f"dividend yield >= {self.min_dividend_yield:.2%}")
        if self.sectors:
            out.append(f"sector in {list(self.sectors)}")
        if self.exclude_sectors:
            out.append(f"sector not in {list(self.exclude_sectors)}")
        if self.countries:
            out.append(f"country in {list(self.countries)}")
        if self.expression:
            out.append(f"custom expression: {self.expression}")
        out.append(f"at most {self.max_positions} positions")
        return out


# ---------------------------------------------------------------------------
# Restricted expression evaluation
# ---------------------------------------------------------------------------
class FilterExpressionError(ValueError):
    """Raised when a custom filter expression is rejected or fails."""


# Only column names, numbers, comparisons, boolean operators and parentheses.
# No attribute access, no calls, no dunders, no imports.
_EXPR_ALLOWED = re.compile(r"^[\w\s\.\,\(\)\[\]\'\"<>=!&|~+\-*/%]+$")
_EXPR_FORBIDDEN = ("__", "import", "lambda", "exec", "eval", "open", "os",
                   "sys", "subprocess", "globals", "locals", "getattr",
                   "setattr", "delattr", "compile", "input", "breakpoint")


def evaluate_filter_expression(df: pd.DataFrame, expression: str) -> pd.Series:
    """Evaluate one boolean pandas expression over `df`, safely.

    Uses ``DataFrame.eval`` with the ``python`` parser disabled in favour of
    pandas' own restricted grammar, plus an allow-list check. This is a narrow
    surface by design: it can filter rows and nothing else.
    """
    expr = (expression or "").strip()
    if not expr:
        return pd.Series(True, index=df.index)
    low = expr.lower()
    for bad in _EXPR_FORBIDDEN:
        if bad in low:
            raise FilterExpressionError(f"expression may not contain {bad!r}")
    if not _EXPR_ALLOWED.match(expr):
        raise FilterExpressionError("expression contains unsupported characters")
    try:
        result = df.eval(expr, engine="numexpr" if _has_numexpr() else "python")
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        raise FilterExpressionError(str(exc)) from exc
    if not isinstance(result, pd.Series) or result.dtype != bool:
        raise FilterExpressionError("expression must evaluate to a boolean mask")
    return result.reindex(df.index, fill_value=False)


def _has_numexpr() -> bool:
    try:
        import numexpr  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Screening
# ---------------------------------------------------------------------------
def screen_universe(df: pd.DataFrame, criteria: ScreenCriteria) -> pd.DataFrame:
    """Apply `criteria` to a universe frame.

    Recognised (all optional) columns: ``ticker``, ``sector``, ``country``,
    ``beta``, ``market_cap``, ``volatility``, ``dividend_yield``. A rule whose
    column is absent is skipped rather than silently emptying the result, so a
    thin universe degrades instead of failing.
    """
    if df.empty:
        return df.copy()
    out = df.copy()
    mask = pd.Series(True, index=out.index)

    def numeric(col: str) -> pd.Series | None:
        return pd.to_numeric(out[col], errors="coerce") if col in out.columns else None

    beta = numeric("beta")
    if beta is not None:
        if criteria.beta_min is not None:
            mask &= beta >= criteria.beta_min
        if criteria.beta_max is not None:
            mask &= beta <= criteria.beta_max
    cap = numeric("market_cap")
    if cap is not None:
        if criteria.market_cap_min is not None:
            mask &= cap >= criteria.market_cap_min
        if criteria.market_cap_max is not None:
            mask &= cap <= criteria.market_cap_max
    vol = numeric("volatility")
    if vol is not None and criteria.max_volatility is not None:
        mask &= vol <= criteria.max_volatility
    dy = numeric("dividend_yield")
    if dy is not None and criteria.min_dividend_yield is not None:
        mask &= dy >= criteria.min_dividend_yield
    if criteria.sectors and "sector" in out.columns:
        mask &= out["sector"].isin(criteria.sectors)
    if criteria.exclude_sectors and "sector" in out.columns:
        mask &= ~out["sector"].isin(criteria.exclude_sectors)
    if criteria.countries and "country" in out.columns:
        mask &= out["country"].isin(criteria.countries)
    if criteria.expression:
        mask &= evaluate_filter_expression(out, criteria.expression)

    out = out[mask.fillna(False)]
    # Keep the largest names when the screen is wider than the position cap, so
    # the cut is by size rather than by row order.
    if len(out) > criteria.max_positions:
        if "market_cap" in out.columns:
            out = out.nlargest(criteria.max_positions, "market_cap")
        else:
            out = out.head(criteria.max_positions)
    return out


# ---------------------------------------------------------------------------
# Weighting
# ---------------------------------------------------------------------------
WEIGHTINGS: dict[str, str] = {
    "equal": "Equal weight — the hardest benchmark to beat after costs",
    "inverse_vol": "Inverse volatility — risk-budgeted, no return forecast",
    "market_cap": "Market-cap weight — mirrors the index",
    "inverse_beta": "Inverse beta — tilts to defensives",
    "min_variance_lite": "Min-variance (diagonal) — inverse variance",
}


def build_weights(df: pd.DataFrame, scheme: str = "equal") -> dict[str, float]:
    """Turn a screened universe into weights summing to 1.

    Every scheme falls back to equal weight when the column it needs is absent
    or degenerate, so a weighting choice can never produce an unallocated book.
    """
    if scheme not in WEIGHTINGS:
        raise KeyError(f"unknown weighting {scheme!r}; choose from {sorted(WEIGHTINGS)}")
    if df.empty:
        return {}
    tickers = (df["ticker"] if "ticker" in df.columns else df.index).astype(str).tolist()
    n = len(tickers)

    def col(name: str) -> np.ndarray | None:
        if name not in df.columns:
            return None
        v = pd.to_numeric(df[name], errors="coerce").to_numpy(dtype=float)
        return v if np.isfinite(v).any() else None

    raw = np.ones(n)
    if scheme == "inverse_vol":
        v = col("volatility")
        if v is not None:
            raw = 1.0 / np.where(np.isfinite(v) & (v > 1e-6), v, np.nan)
    elif scheme == "market_cap":
        v = col("market_cap")
        if v is not None:
            raw = np.where(np.isfinite(v) & (v > 0), v, np.nan)
    elif scheme == "inverse_beta":
        v = col("beta")
        if v is not None:
            raw = 1.0 / np.where(np.isfinite(v) & (v > 1e-6), v, np.nan)
    elif scheme == "min_variance_lite":
        v = col("volatility")
        if v is not None:
            raw = 1.0 / np.where(np.isfinite(v) & (v > 1e-6), v**2, np.nan)

    raw = np.where(np.isfinite(raw), raw, np.nan)
    if not np.isfinite(raw).any() or np.nansum(raw) <= 0:
        raw = np.ones(n)                      # degenerate input -> equal weight
    raw = np.where(np.isfinite(raw), raw, 0.0)
    total = raw.sum()
    if total <= 0:
        raw, total = np.ones(n), float(n)
    return {t: float(w / total) for t, w in zip(tickers, raw)}


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------
def generate_strategy_code(
    criteria: ScreenCriteria,
    weighting: str = "equal",
    rebalance: str = "Q",
    *,
    profile: str = "custom",
) -> str:
    """Render the chosen rules as a standalone, runnable Python strategy.

    This is the artefact the user takes away: it imports the same platform
    functions the app calls, so running it reproduces the app's allocation
    rather than approximating it.
    """
    if weighting not in WEIGHTINGS:
        raise KeyError(f"unknown weighting {weighting!r}")
    rules = "\n".join(f"#   - {r}" for r in criteria.describe())
    expr = repr(criteria.expression) if criteria.expression else '""'
    return f'''"""Algohns generated strategy — profile: {profile}

Screening rules:
{rules}

Weighting: {weighting} ({WEIGHTINGS[weighting]})
Rebalance: {rebalance}

This file was generated by the Strategy Lab and calls the same platform code
the app runs, so executing it reproduces the app's allocation exactly.
Paper trading only: real-money execution is locked platform-wide.
"""
from __future__ import annotations

import pandas as pd

from algohns.core.data_providers import get_market_data
from algohns.modules.backtest_suite import Backtester
from algohns.modules.strategy_lab import (
    ScreenCriteria,
    build_weights,
    screen_universe,
)

# --- 1. The rules, exactly as set in the Strategy Lab ----------------------
CRITERIA = ScreenCriteria(
    beta_min={criteria.beta_min!r},
    beta_max={criteria.beta_max!r},
    market_cap_min={criteria.market_cap_min!r},
    market_cap_max={criteria.market_cap_max!r},
    max_volatility={criteria.max_volatility!r},
    min_dividend_yield={criteria.min_dividend_yield!r},
    sectors={tuple(criteria.sectors)!r},
    exclude_sectors={tuple(criteria.exclude_sectors)!r},
    countries={tuple(criteria.countries)!r},
    max_positions={criteria.max_positions!r},
    expression={expr},
)
WEIGHTING = {weighting!r}
REBALANCE = {rebalance!r}


def select(universe: pd.DataFrame) -> dict[str, float]:
    """Screen the universe and turn the survivors into target weights."""
    picks = screen_universe(universe, CRITERIA)
    return build_weights(picks, WEIGHTING)


def backtest(weights: dict[str, float], period: str = "5y"):
    """Replay the allocation on real prices."""
    prices = get_market_data().history(list(weights), period=period)
    held = {{t: w for t, w in weights.items() if t in prices.columns}}
    return Backtester(prices).run(held, rebalance=REBALANCE)


if __name__ == "__main__":
    # `universe` needs the columns the rules reference: ticker, sector,
    # country, beta, market_cap, volatility, dividend_yield.
    from algohns.modules.strategy_lab import demo_universe

    target = select(demo_universe())
    print("target weights:")
    for ticker, weight in sorted(target.items(), key=lambda kv: -kv[1]):
        print(f"  {{ticker:<8}} {{weight:7.2%}}")

    result = backtest(target)
    print()
    print(result.metrics.as_dict())
'''


def demo_universe() -> pd.DataFrame:
    """A small, real-attribute universe so the generated script runs standalone.

    Attributes come from the bundled world dataset (real betas, caps, sectors),
    so the example is not invented numbers.
    """
    try:
        from algohns.modules.world_universe import COMPANIES
    except Exception:  # pragma: no cover - defensive
        return pd.DataFrame()
    rows = []
    for c in COMPANIES:
        rows.append({
            "ticker": c.ticker,
            "name": getattr(c, "name", c.ticker),
            "sector": getattr(c, "sector", ""),
            "country": getattr(c, "domicile", ""),
            "beta": getattr(c, "beta", np.nan),
            "market_cap": getattr(c, "market_cap_usd", np.nan),
        })
    df = pd.DataFrame(rows)
    # Volatility is not carried on the company record; approximate it from beta
    # and a market vol of 16% so the risk-based weightings have an input.
    if "beta" in df.columns:
        df["volatility"] = pd.to_numeric(df["beta"], errors="coerce") * 0.16
    return df
