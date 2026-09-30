"""Stochastic price- and rate-formation processes for the forward simulation.

This is the quantitative core of the forward test. Every process is written in
the standard form found in the literature, with the discretisation stated, so
the model can be read and argued with rather than taken on trust.

**Why several processes.** A single geometric Brownian motion is the wrong
default for a forward test that claims to be institutional:

* GBM has constant volatility, so it cannot produce the volatility clustering
  that dominates realised equity risk;
* its increments are Gaussian, so it under-prices tails badly — the moves that
  decide whether a portfolio survives;
* it has no mechanism for a discrete repricing, which is exactly what a news
  shock is.

So the engine offers, all seeded and all validated against their analytic
moments in ``tests/test_stochastic.py``:

===========================  ====================================================
Process                      Use
===========================  ====================================================
``gbm``                      Geometric Brownian motion — the baseline.
``student_t``                GBM with Student-t shocks — fat tails, same drift.
``merton``                   Merton (1976) jump-diffusion — news as Poisson jumps.
``heston``                   Heston (1993) stochastic variance — vol clustering.
``vasicek``                  Vasicek (1977) short rate — mean-reverting, Gaussian.
``cir``                      Cox-Ingersoll-Ross short rate — mean-reverting, >= 0.
===========================  ====================================================

**The martingale discipline.** Every equity process is written so that

    E[S_T] = S_0 * exp(mu * T)

holds *whatever* the volatility, tail or jump parameters are. That is what the
``-0.5 sigma^2`` term does in GBM, and what the ``-lambda * k`` compensator
does in Merton: without it, adding jumps or raising vol would quietly change
the expected return and every comparison between two parameter sets would be
confounded. The tests assert this property directly.

**News-modulated dynamics.** ``NewsModulation`` is how the world's events reach
the price process: an event does not overwrite the path, it *shifts the
parameters* of the process generating it — drift, volatility and jump
intensity. So the price keeps following its stochastic law while the news
decides the regime it follows it in, which is the behaviour the forward test
is meant to have.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = [
    "NewsModulation",
    "ProcessParams",
    "gbm_paths",
    "student_t_paths",
    "merton_paths",
    "heston_paths",
    "vasicek_paths",
    "cir_paths",
    "PROCESSES",
    "simulate_paths",
    "terminal_statistics",
]


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProcessParams:
    """Annualised parameters shared by the price processes.

    `mu` and `sigma` are annualised; `steps_per_year` sets the discretisation.
    Jump and variance parameters are ignored by processes that do not use them,
    which keeps one parameter object usable across the whole registry.
    """

    mu: float = 0.07               # annual expected log-growth target
    sigma: float = 0.20            # annual diffusion volatility
    steps_per_year: int = 252

    # --- Student-t tails ---------------------------------------------------
    df: float = 4.0                # degrees of freedom (> 2 for finite variance)

    # --- Merton jump-diffusion --------------------------------------------
    jump_intensity: float = 0.0    # lambda: expected jumps per year
    jump_mean: float = -0.02       # m: mean of log jump size
    jump_vol: float = 0.10         # s: std of log jump size

    # --- Heston stochastic variance ---------------------------------------
    kappa: float = 2.0             # variance mean-reversion speed
    theta: float = 0.04            # long-run variance (= 0.20^2)
    xi: float = 0.30               # vol-of-vol
    rho: float = -0.7              # spot/vol correlation (leverage effect)

    def __post_init__(self) -> None:
        if self.steps_per_year <= 0:
            raise ValueError("steps_per_year must be positive")
        if self.sigma < 0:
            raise ValueError("sigma cannot be negative")
        if self.df <= 2:
            raise ValueError("Student-t df must exceed 2 for a finite variance")
        if self.jump_intensity < 0:
            raise ValueError("jump_intensity cannot be negative")
        if self.kappa < 0 or self.theta < 0 or self.xi < 0:
            raise ValueError("Heston kappa/theta/xi cannot be negative")
        if not -1.0 <= self.rho <= 1.0:
            raise ValueError("rho must lie in [-1, 1]")


@dataclass
class NewsModulation:
    """How the world's news bends the process parameters over time.

    Arrays are per-step and broadcast over paths. This is the coupling the
    forward test needs: news does not paint the path, it re-parameterises the
    law the path is drawn from.

    * ``drift_shift``      additive, annualised, on ``mu``
    * ``vol_multiplier``   multiplicative on ``sigma`` (>= 0)
    * ``intensity_shift``  additive on the jump intensity ``lambda`` (>= 0 after clipping)
    """

    drift_shift: np.ndarray | None = None
    vol_multiplier: np.ndarray | None = None
    intensity_shift: np.ndarray | None = None

    def resolve(self, n_steps: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return (drift_shift, vol_multiplier, intensity_shift) of length n_steps."""
        def _arr(a: np.ndarray | None, fill: float) -> np.ndarray:
            if a is None:
                return np.full(n_steps, fill, dtype=float)
            out = np.asarray(a, dtype=float).ravel()
            if out.size == n_steps:
                return out
            if out.size == 1:
                return np.full(n_steps, float(out[0]))
            # Resample by nearest index so a coarser news clock still applies.
            idx = np.clip((np.arange(n_steps) * out.size) // max(n_steps, 1), 0, out.size - 1)
            return out[idx]

        vol = np.maximum(_arr(self.vol_multiplier, 1.0), 0.0)
        lam = _arr(self.intensity_shift, 0.0)
        return _arr(self.drift_shift, 0.0), vol, lam


def _shape(n_paths: int, n_steps: int) -> tuple[int, int]:
    if n_paths < 1 or n_steps < 1:
        raise ValueError("n_paths and n_steps must be >= 1")
    return n_paths, n_steps


# ---------------------------------------------------------------------------
# Equity processes.  Each returns an (n_paths, n_steps + 1) array of levels
# starting at s0, so column i is the level after i steps.
# ---------------------------------------------------------------------------
def gbm_paths(
    s0: float, p: ProcessParams, n_paths: int, n_steps: int,
    rng: np.random.Generator, news: NewsModulation | None = None,
) -> np.ndarray:
    """Geometric Brownian motion, exact log-Euler.

        d(log S) = (mu - sigma^2 / 2) dt + sigma dW

    The Ito correction keeps E[S_T] = S_0 e^{mu T}.
    """
    n_paths, n_steps = _shape(n_paths, n_steps)
    dt = 1.0 / p.steps_per_year
    d_mu, vol_mult, _ = (news or NewsModulation()).resolve(n_steps)
    sig = p.sigma * vol_mult
    mu = p.mu + d_mu
    z = rng.standard_normal((n_paths, n_steps))
    incr = (mu - 0.5 * sig**2) * dt + sig * np.sqrt(dt) * z
    return _accumulate(s0, incr)


def student_t_paths(
    s0: float, p: ProcessParams, n_paths: int, n_steps: int,
    rng: np.random.Generator, news: NewsModulation | None = None,
) -> np.ndarray:
    """GBM with Student-t shocks: same drift and variance, much fatter tails.

    The raw t has variance df / (df - 2), so it is scaled to unit variance
    before entering the diffusion term. The Ito correction then uses the same
    sigma as GBM, which makes the two directly comparable: identical mean and
    variance, different tail mass.
    """
    n_paths, n_steps = _shape(n_paths, n_steps)
    dt = 1.0 / p.steps_per_year
    d_mu, vol_mult, _ = (news or NewsModulation()).resolve(n_steps)
    sig = p.sigma * vol_mult
    mu = p.mu + d_mu
    t_raw = rng.standard_t(p.df, size=(n_paths, n_steps))
    z = t_raw / np.sqrt(p.df / (p.df - 2.0))          # unit variance
    incr = (mu - 0.5 * sig**2) * dt + sig * np.sqrt(dt) * z
    return _accumulate(s0, incr)


def merton_paths(
    s0: float, p: ProcessParams, n_paths: int, n_steps: int,
    rng: np.random.Generator, news: NewsModulation | None = None,
) -> np.ndarray:
    """Merton (1976) jump-diffusion — the natural home for news shocks.

        dS/S = (mu - lambda k) dt + sigma dW + (J - 1) dN

    with N Poisson(lambda) and log J ~ N(m, s^2), so k = E[J] - 1 =
    e^{m + s^2/2} - 1. Subtracting ``lambda k`` is the compensator: it is what
    keeps E[S_T] = S_0 e^{mu T} when jumps are switched on, so turning news up
    changes the *risk*, not the expected return.
    """
    n_paths, n_steps = _shape(n_paths, n_steps)
    dt = 1.0 / p.steps_per_year
    d_mu, vol_mult, d_lam = (news or NewsModulation()).resolve(n_steps)
    sig = p.sigma * vol_mult
    mu = p.mu + d_mu
    lam = np.maximum(p.jump_intensity + d_lam, 0.0)

    k = np.exp(p.jump_mean + 0.5 * p.jump_vol**2) - 1.0      # E[J] - 1
    z = rng.standard_normal((n_paths, n_steps))
    # Number of jumps per step, then their summed log size.
    counts = rng.poisson(lam * dt, size=(n_paths, n_steps))
    # Sum of `counts` iid normals is normal(count*m, count*s^2).
    jump_log = np.where(
        counts > 0,
        counts * p.jump_mean
        + np.sqrt(np.maximum(counts, 0)) * p.jump_vol * rng.standard_normal((n_paths, n_steps)),
        0.0,
    )
    incr = (mu - lam * k - 0.5 * sig**2) * dt + sig * np.sqrt(dt) * z + jump_log
    return _accumulate(s0, incr)


def heston_paths(
    s0: float, p: ProcessParams, n_paths: int, n_steps: int,
    rng: np.random.Generator, news: NewsModulation | None = None,
    v0: float | None = None,
) -> np.ndarray:
    """Heston (1993) stochastic variance, full-truncation Euler.

        dS/S = mu dt + sqrt(v) dW1
        dv   = kappa (theta - v) dt + xi sqrt(v) dW2,   corr(dW1, dW2) = rho

    Full truncation (clamp v at 0 inside the diffusion) is the standard fix for
    Euler schemes on the CIR variance, which can otherwise go negative. A
    negative ``rho`` gives the leverage effect: variance rises as price falls,
    which is why drawdowns in this process cluster the way real ones do.
    """
    n_paths, n_steps = _shape(n_paths, n_steps)
    dt = 1.0 / p.steps_per_year
    d_mu, vol_mult, _ = (news or NewsModulation()).resolve(n_steps)
    mu = p.mu + d_mu
    # News scales the variance level, so vol_multiplier stays comparable to GBM.
    var_mult = vol_mult**2
    v = np.full(n_paths, float(p.theta if v0 is None else v0))
    log_s = np.zeros((n_paths, n_steps + 1))
    sqrt_dt = np.sqrt(dt)
    for i in range(n_steps):
        z1 = rng.standard_normal(n_paths)
        z2 = p.rho * z1 + np.sqrt(max(1.0 - p.rho**2, 0.0)) * rng.standard_normal(n_paths)
        v_pos = np.maximum(v, 0.0) * var_mult[i]
        log_s[:, i + 1] = (
            log_s[:, i] + (mu[i] - 0.5 * v_pos) * dt + np.sqrt(v_pos) * sqrt_dt * z1
        )
        v = v + p.kappa * (p.theta - np.maximum(v, 0.0)) * dt \
            + p.xi * np.sqrt(np.maximum(v, 0.0)) * sqrt_dt * z2
    return float(s0) * np.exp(log_s)


# ---------------------------------------------------------------------------
# Short-rate processes.  Returned as (n_paths, n_steps + 1) rate levels.
# ---------------------------------------------------------------------------
def vasicek_paths(
    r0: float, kappa: float, theta: float, sigma: float,
    n_paths: int, n_steps: int, steps_per_year: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Vasicek (1977) short rate, **exact** discretisation.

        dr = kappa (theta - r) dt + sigma dW

    Exact because the SDE is an Ornstein-Uhlenbeck process, whose transition
    law is Gaussian in closed form:

        r_{t+dt} = r_t e^{-kappa dt} + theta (1 - e^{-kappa dt})
                   + sigma sqrt((1 - e^{-2 kappa dt}) / (2 kappa)) Z

    so there is no discretisation bias at any step size. Stationary law:
    mean ``theta``, variance ``sigma^2 / (2 kappa)``. Rates can go negative —
    which, post-2014 Europe, is a feature, not a bug.
    """
    n_paths, n_steps = _shape(n_paths, n_steps)
    if kappa <= 0:
        raise ValueError("kappa must be positive for mean reversion")
    dt = 1.0 / steps_per_year
    a = np.exp(-kappa * dt)
    sd = sigma * np.sqrt((1.0 - np.exp(-2.0 * kappa * dt)) / (2.0 * kappa))
    out = np.empty((n_paths, n_steps + 1))
    out[:, 0] = r0
    for i in range(n_steps):
        out[:, i + 1] = (
            out[:, i] * a + theta * (1.0 - a) + sd * rng.standard_normal(n_paths)
        )
    return out


def cir_paths(
    r0: float, kappa: float, theta: float, sigma: float,
    n_paths: int, n_steps: int, steps_per_year: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Cox-Ingersoll-Ross short rate, full-truncation Euler.

        dr = kappa (theta - r) dt + sigma sqrt(r) dW

    The sqrt(r) diffusion sends volatility to zero as the rate approaches zero,
    so the rate stays non-negative (strictly positive when the Feller condition
    2 kappa theta >= sigma^2 holds). Use this instead of Vasicek when a
    negative nominal rate would be wrong for the market being modelled.
    """
    n_paths, n_steps = _shape(n_paths, n_steps)
    if kappa <= 0:
        raise ValueError("kappa must be positive for mean reversion")
    dt = 1.0 / steps_per_year
    sqrt_dt = np.sqrt(dt)
    out = np.empty((n_paths, n_steps + 1))
    out[:, 0] = max(float(r0), 0.0)
    for i in range(n_steps):
        r = np.maximum(out[:, i], 0.0)
        out[:, i + 1] = np.maximum(
            r + kappa * (theta - r) * dt
            + sigma * np.sqrt(r) * sqrt_dt * rng.standard_normal(n_paths),
            0.0,
        )
    return out


def feller_condition(kappa: float, theta: float, sigma: float) -> bool:
    """True when 2*kappa*theta >= sigma^2, i.e. CIR cannot reach zero."""
    return 2.0 * kappa * theta >= sigma**2


# ---------------------------------------------------------------------------
# Registry + helpers
# ---------------------------------------------------------------------------
def _accumulate(s0: float, log_increments: np.ndarray) -> np.ndarray:
    """Turn per-step log increments into a level path starting at s0."""
    log_path = np.concatenate(
        [np.zeros((log_increments.shape[0], 1)), np.cumsum(log_increments, axis=1)],
        axis=1,
    )
    return float(s0) * np.exp(log_path)


PROCESSES: dict[str, str] = {
    "gbm": "Geometric Brownian motion (baseline)",
    "student_t": "GBM with Student-t shocks (fat tails)",
    "merton": "Merton jump-diffusion (news as jumps)",
    "heston": "Heston stochastic variance (vol clustering)",
}


def simulate_paths(
    process: str, s0: float, p: ProcessParams, n_paths: int, n_steps: int,
    rng: np.random.Generator, news: NewsModulation | None = None,
) -> np.ndarray:
    """Dispatch to an equity process by name (see ``PROCESSES``)."""
    fns = {
        "gbm": gbm_paths,
        "student_t": student_t_paths,
        "merton": merton_paths,
        "heston": heston_paths,
    }
    if process not in fns:
        raise KeyError(f"unknown process {process!r}; choose from {sorted(fns)}")
    return fns[process](s0, p, n_paths, n_steps, rng, news)


def terminal_statistics(paths: np.ndarray) -> dict[str, float]:
    """Monte Carlo summary of terminal levels, with the risk tails.

    Reports the quantiles a risk committee actually reads: the 5% VaR and the
    expected shortfall beyond it, alongside the mean and median. The gap
    between mean and median is the log-normality of the process made visible.
    """
    terminal = np.asarray(paths)[:, -1]
    start = np.asarray(paths)[:, 0]
    total_return = terminal / start - 1.0
    running_max = np.maximum.accumulate(np.asarray(paths), axis=1)
    max_dd = float(np.min(np.asarray(paths) / running_max - 1.0))
    q05 = float(np.quantile(total_return, 0.05))
    tail = total_return[total_return <= q05]
    return {
        "mean_return": float(total_return.mean()),
        "median_return": float(np.median(total_return)),
        "std_return": float(total_return.std(ddof=1)) if total_return.size > 1 else 0.0,
        "var_95": q05,
        "expected_shortfall_95": float(tail.mean()) if tail.size else q05,
        "worst_return": float(total_return.min()),
        "best_return": float(total_return.max()),
        "prob_loss": float((total_return < 0).mean()),
        "max_drawdown": max_dd,
        "paths": int(terminal.size),
    }
