"""Portfolio optimisation: Max Sharpe, Min Variance, Risk Parity, HRP, Black-Litterman.

Implemented natively on SciPy so the module works without PyPortfolioOpt.
`pypfopt_available()` lets callers cross-check against that library when it
is installed — useful because silent disagreement between two optimisers is
much harder to debug than an explicit comparison.

Covariance is annualised before optimisation. Shrinkage (Ledoit-Wolf) is the
default because sample covariance on a few hundred observations and 50+
assets is close to singular, and the resulting weights are unstable garbage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, to_tree
from scipy.optimize import minimize
from scipy.spatial.distance import squareform

from modules.metrics import TRADING_DAYS

__all__ = [
    "Objective",
    "OptimizationResult",
    "optimize",
    "black_litterman_returns",
    "expected_returns",
    "covariance",
    "pypfopt_available",
]


class Objective(str, Enum):
    MAX_SHARPE = "max_sharpe"
    MIN_VARIANCE = "min_variance"
    RISK_PARITY = "risk_parity"
    HRP = "hrp"
    MAX_RETURN = "max_return"
    EQUAL_WEIGHT = "equal_weight"


@dataclass
class OptimizationResult:
    weights: dict[str, float]
    expected_return: float
    volatility: float
    sharpe: float
    objective: Objective
    risk_contributions: dict[str, float] = field(default_factory=dict)
    converged: bool = True
    message: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def effective_n(self) -> float:
        """Inverse Herfindahl: how many names the portfolio *effectively* holds.

        A 50-name portfolio with an effective N of 3 is a concentrated bet
        wearing a diversified costume.
        """
        w = np.array(list(self.weights.values()))
        denom = float((w**2).sum())
        return 1.0 / denom if denom > 0 else 0.0

    def nonzero(self, threshold: float = 1e-4) -> dict[str, float]:
        return {k: v for k, v in self.weights.items() if abs(v) > threshold}

    def to_series(self) -> pd.Series:
        return pd.Series(self.weights).sort_values(ascending=False)


def pypfopt_available() -> bool:
    try:
        import pypfopt  # type: ignore  # noqa: F401

        return True
    except ImportError:
        return False


def expected_returns(
    prices: pd.DataFrame,
    *,
    method: str = "mean",
    periods_per_year: int = TRADING_DAYS,
    span: int = 500,
) -> pd.Series:
    """Annualised expected returns.

    `mean` is the historical arithmetic mean; `ewm` weights recent data more
    heavily, which is usually the lesser evil since the far past is a poor
    guide to the next quarter. Neither is a forecast — treat both as a prior
    to be overridden via Black-Litterman views.
    """
    rets = prices.pct_change().dropna(how="all")
    if rets.empty:
        raise ValueError("serie prezzi troppo corta per stimare i rendimenti")
    if method == "ewm":
        mu = rets.ewm(span=span).mean().iloc[-1]
    elif method == "mean":
        mu = rets.mean()
    elif method == "median":
        mu = rets.median()
    else:
        raise ValueError(f"metodo sconosciuto: {method!r}")
    return mu * periods_per_year


def covariance(
    prices: pd.DataFrame,
    *,
    shrinkage: bool = True,
    periods_per_year: int = TRADING_DAYS,
) -> pd.DataFrame:
    """Annualised covariance matrix, Ledoit-Wolf shrunk by default."""
    rets = prices.pct_change().dropna(how="all")
    if len(rets) < 2:
        raise ValueError("servono almeno 2 rendimenti per la covarianza")
    sample = rets.cov() * periods_per_year
    if not shrinkage:
        return sample
    return _ledoit_wolf(rets, sample, periods_per_year)


def _ledoit_wolf(
    rets: pd.DataFrame, sample: pd.DataFrame, periods_per_year: int
) -> pd.DataFrame:
    """Shrink sample covariance toward a constant-correlation target.

    Uses the Ledoit-Wolf single-index intensity. When observations are few
    relative to assets the intensity approaches 1 and the estimate collapses
    onto the structured target, which is exactly the desired behaviour.
    """
    x = rets.dropna().to_numpy()
    n, p = x.shape
    if n <= 1 or p == 0:
        return sample

    s = sample.to_numpy()
    var = np.diag(s)
    std = np.sqrt(np.clip(var, 1e-18, None))
    corr = s / np.outer(std, std)
    off = corr[~np.eye(p, dtype=bool)]
    mean_corr = float(off.mean()) if off.size else 0.0

    target = mean_corr * np.outer(std, std)
    np.fill_diagonal(target, var)

    # Shrinkage intensity from the dispersion of the sample estimator.
    xc = x - x.mean(axis=0)
    phi = 0.0
    for t in range(n):
        d = np.outer(xc[t], xc[t]) * periods_per_year - s
        phi += float((d**2).sum())
    phi /= n
    gamma = float(((s - target) ** 2).sum())
    intensity = 0.0 if gamma <= 0 else max(0.0, min(1.0, (phi / n) / gamma))

    shrunk = intensity * target + (1.0 - intensity) * s
    return pd.DataFrame(shrunk, index=sample.index, columns=sample.columns)


def _portfolio_stats(
    w: np.ndarray, mu: np.ndarray, cov: np.ndarray, rf: float
) -> tuple[float, float, float]:
    ret = float(w @ mu)
    var = float(w @ cov @ w)
    vol = float(np.sqrt(max(var, 0.0)))
    sharpe = (ret - rf) / vol if vol > 1e-12 else 0.0
    return ret, vol, sharpe


def _risk_contributions(w: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Each asset's share of total portfolio volatility."""
    var = float(w @ cov @ w)
    if var <= 0:
        return np.zeros_like(w)
    marginal = cov @ w
    return (w * marginal) / var


def optimize(
    mu: pd.Series,
    cov: pd.DataFrame,
    *,
    objective: Objective = Objective.MAX_SHARPE,
    risk_free_rate: float = 0.0,
    weight_bounds: tuple[float, float] = (0.0, 1.0),
    target_return: float | None = None,
    max_weight_per_asset: float | None = None,
) -> OptimizationResult:
    """Solve for optimal weights under a long-only budget constraint.

    `weight_bounds` of (0, 1) means long-only fully-invested. Shorts are
    possible by passing a negative lower bound, but the risk-parity and HRP
    objectives assume non-negative weights and will warn.
    """
    assets = list(mu.index)
    if list(cov.index) != assets:
        cov = cov.loc[assets, assets]
    mu_v = mu.to_numpy(dtype=float)
    cov_v = cov.to_numpy(dtype=float)
    n = len(assets)
    if n == 0:
        raise ValueError("nessun asset fornito")

    notes: list[str] = []
    # A non-PSD covariance makes every objective below meaningless.
    eigmin = float(np.linalg.eigvalsh(cov_v).min())
    if eigmin < -1e-10:
        notes.append(
            f"Covarianza non semidefinita positiva (autovalore min {eigmin:.2e}); "
            "applicata correzione diagonale."
        )
        cov_v = cov_v + np.eye(n) * (abs(eigmin) + 1e-10)

    lo, hi = weight_bounds
    if max_weight_per_asset is not None:
        hi = min(hi, max_weight_per_asset)
    if hi * n < 1.0 - 1e-9:
        raise ValueError(
            f"peso massimo {hi:.3f} x {n} asset < 1.0: budget non soddisfacibile"
        )
    bounds = [(lo, hi)] * n

    if objective is Objective.EQUAL_WEIGHT:
        w = np.full(n, 1.0 / n)
        return _build_result(w, assets, mu_v, cov_v, risk_free_rate, objective, notes=notes)

    if objective is Objective.HRP:
        if lo < 0:
            notes.append("HRP ignora i bound negativi: pesi long-only.")
        w = _hrp_weights(cov_v)
        return _build_result(w, assets, mu_v, cov_v, risk_free_rate, objective, notes=notes)

    constraints: list[dict] = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    if target_return is not None:
        constraints.append(
            {"type": "eq", "fun": lambda w, t=target_return: float(w @ mu_v) - t}
        )

    if objective is Objective.MAX_SHARPE:
        def fn(w: np.ndarray) -> float:
            _, _, s = _portfolio_stats(w, mu_v, cov_v, risk_free_rate)
            return -s
    elif objective is Objective.MIN_VARIANCE:
        def fn(w: np.ndarray) -> float:
            return float(w @ cov_v @ w)
    elif objective is Objective.MAX_RETURN:
        def fn(w: np.ndarray) -> float:
            return -float(w @ mu_v)
    elif objective is Objective.RISK_PARITY:
        if lo < 0:
            notes.append("Risk parity richiede pesi non negativi; bound alzato a 0.")
            bounds = [(0.0, hi)] * n

        def fn(w: np.ndarray) -> float:
            # Equalise risk contributions: penalise dispersion around 1/n.
            rc = _risk_contributions(w, cov_v)
            return float(((rc - 1.0 / n) ** 2).sum())
    else:
        raise ValueError(f"obiettivo non gestito: {objective}")

    best: tuple[float, np.ndarray] | None = None
    message = ""
    # Multi-start: SLSQP on a non-convex Sharpe surface is start-dependent.
    for x0 in _starting_points(n, bounds):
        res = minimize(
            fn, x0, method="SLSQP", bounds=bounds, constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-12},
        )
        if res.success and np.isfinite(res.fun):
            if best is None or res.fun < best[0]:
                best = (float(res.fun), res.x)
        else:
            message = str(res.message)

    if best is None:
        notes.append(
            f"Ottimizzazione non convergente ({message or 'nessuna soluzione'}); "
            "fallback su equal weight."
        )
        return _build_result(
            np.full(n, 1.0 / n), assets, mu_v, cov_v, risk_free_rate, objective,
            converged=False, message=message, notes=notes,
        )

    w = np.clip(best[1], lo, hi)
    total = w.sum()
    if total <= 0:
        raise ValueError("i pesi ottimizzati sommano a zero")
    w = w / total
    return _build_result(
        w, assets, mu_v, cov_v, risk_free_rate, objective, notes=notes
    )


def _starting_points(n: int, bounds: list[tuple[float, float]]) -> list[np.ndarray]:
    """Deterministic multi-start set: equal weight plus corner-biased seeds."""
    pts = [np.full(n, 1.0 / n)]
    rng = np.random.default_rng(0)  # fixed seed: optimisation must be reproducible
    for _ in range(4):
        v = rng.random(n)
        pts.append(v / v.sum())
    return pts


def _build_result(
    w: np.ndarray,
    assets: list[str],
    mu: np.ndarray,
    cov: np.ndarray,
    rf: float,
    objective: Objective,
    *,
    converged: bool = True,
    message: str = "",
    notes: list[str] | None = None,
) -> OptimizationResult:
    ret, vol, sharpe = _portfolio_stats(w, mu, cov, rf)
    rc = _risk_contributions(w, cov)
    return OptimizationResult(
        weights={a: float(x) for a, x in zip(assets, w)},
        expected_return=ret,
        volatility=vol,
        sharpe=sharpe,
        objective=objective,
        risk_contributions={a: float(x) for a, x in zip(assets, rc)},
        converged=converged,
        message=message,
        notes=notes or [],
    )


def _hrp_weights(cov: np.ndarray) -> np.ndarray:
    """Hierarchical Risk Parity (Lopez de Prado 2016).

    Clusters assets on correlation distance, then splits capital recursively
    by inverse cluster variance. Needs no return forecast and no matrix
    inversion, which is why it stays stable where Max Sharpe does not.
    """
    n = cov.shape[0]
    if n == 1:
        return np.array([1.0])

    std = np.sqrt(np.clip(np.diag(cov), 1e-18, None))
    corr = cov / np.outer(std, std)
    corr = np.clip(corr, -1.0, 1.0)
    dist = np.sqrt(np.clip((1.0 - corr) / 2.0, 0.0, None))
    np.fill_diagonal(dist, 0.0)

    link = linkage(squareform(dist, checks=False), method="single")
    order = _quasi_diagonal(link, n)

    w = np.ones(n)
    clusters = [order]
    while clusters:
        nxt: list[list[int]] = []
        for cl in clusters:
            if len(cl) <= 1:
                continue
            mid = len(cl) // 2
            left, right = cl[:mid], cl[mid:]
            v_left = _cluster_variance(cov, left)
            v_right = _cluster_variance(cov, right)
            total = v_left + v_right
            # Inverse-variance split; equal split if both are degenerate.
            alpha = 1.0 - v_left / total if total > 0 else 0.5
            for i in left:
                w[i] *= alpha
            for i in right:
                w[i] *= 1.0 - alpha
            nxt.extend([left, right])
        clusters = nxt
    return w / w.sum()


def _quasi_diagonal(link: np.ndarray, n: int) -> list[int]:
    """Leaf order from the linkage tree, so correlated assets sit adjacent."""
    tree = to_tree(link)
    order: list[int] = []

    def walk(node) -> None:
        if node.is_leaf():
            order.append(int(node.get_id()))
            return
        walk(node.get_left())
        walk(node.get_right())

    walk(tree)
    return order


def _cluster_variance(cov: np.ndarray, idx: list[int]) -> float:
    """Variance of an inverse-variance-weighted sub-portfolio."""
    sub = cov[np.ix_(idx, idx)]
    ivp = 1.0 / np.clip(np.diag(sub), 1e-18, None)
    ivp = ivp / ivp.sum()
    return float(ivp @ sub @ ivp)


def black_litterman_returns(
    cov: pd.DataFrame,
    market_weights: pd.Series,
    views: dict[str, float],
    *,
    view_confidences: dict[str, float] | None = None,
    risk_aversion: float = 2.5,
    tau: float = 0.05,
    risk_free_rate: float = 0.0,
) -> pd.Series:
    """Black-Litterman posterior expected returns.

    Steps:
      1. Reverse-optimise the market portfolio for implied equilibrium
         returns  Pi = delta * Sigma * w_mkt
      2. Blend the prior with absolute views Q through
         E[R] = [(tau*Sigma)^-1 + P' Omega^-1 P]^-1
                [(tau*Sigma)^-1 Pi + P' Omega^-1 Q]

    `views` are *absolute* annual return expectations per asset, e.g.
    {"AAPL": 0.12}. `view_confidences` in (0, 1] scale each view's
    certainty; omitted views default to 0.5. Omega is built proportional to
    view variance (He-Litterman), so a view on a volatile asset is
    automatically treated as less precise.
    """
    assets = list(cov.index)
    unknown = set(views) - set(assets)
    if unknown:
        raise ValueError(f"view su asset non presenti in covarianza: {sorted(unknown)}")
    if not views:
        raise ValueError("nessuna view fornita")

    w_mkt = market_weights.reindex(assets).fillna(0.0).to_numpy(dtype=float)
    if w_mkt.sum() <= 0:
        raise ValueError("i pesi di mercato sommano a zero")
    w_mkt = w_mkt / w_mkt.sum()

    sigma = cov.to_numpy(dtype=float)
    pi = risk_aversion * sigma @ w_mkt  # implied equilibrium excess returns

    view_assets = [a for a in assets if a in views]
    k = len(view_assets)
    p = np.zeros((k, len(assets)))
    q = np.zeros(k)
    for row, asset in enumerate(view_assets):
        p[row, assets.index(asset)] = 1.0
        q[row] = views[asset] - risk_free_rate

    conf = view_confidences or {}
    # He-Litterman: Omega = diag(P tau Sigma P') scaled by (1-confidence).
    base = np.diag(p @ (tau * sigma) @ p.T)
    scale = np.array([
        max(1e-8, (1.0 - min(max(conf.get(a, 0.5), 1e-6), 0.999999)))
        for a in view_assets
    ])
    omega = np.diag(np.clip(base, 1e-12, None) * scale / np.clip(1.0 - scale, 1e-6, None))

    tau_sigma_inv = np.linalg.pinv(tau * sigma)
    omega_inv = np.linalg.pinv(omega)
    posterior_cov = np.linalg.pinv(tau_sigma_inv + p.T @ omega_inv @ p)
    posterior = posterior_cov @ (tau_sigma_inv @ pi + p.T @ omega_inv @ q)
    return pd.Series(posterior + risk_free_rate, index=assets)
