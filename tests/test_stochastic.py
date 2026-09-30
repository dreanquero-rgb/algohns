"""Validation of the stochastic processes against their analytic moments.

A forward simulation is only worth showing if its processes provably do what
the textbook says. Each test below pins a closed-form property, so a regression
in the discretisation fails loudly instead of quietly biasing every result.

All tests are seeded, so they are reproducible rather than statistically lucky.
"""
from __future__ import annotations

import numpy as np
import pytest

from algohns.modules.stochastic import (
    PROCESSES,
    NewsModulation,
    ProcessParams,
    cir_paths,
    feller_condition,
    gbm_paths,
    heston_paths,
    merton_paths,
    simulate_paths,
    student_t_paths,
    terminal_statistics,
    vasicek_paths,
)

N_PATHS = 20_000
STEPS = 252          # one year of daily steps


def _rng(seed: int = 7) -> np.random.Generator:
    return np.random.default_rng(seed)


# --------------------------------------------------------------------- GBM
class TestGBM:
    def test_expected_terminal_matches_closed_form(self):
        """E[S_T] = S_0 e^{mu T} — the Ito correction must be exactly right."""
        p = ProcessParams(mu=0.07, sigma=0.20, steps_per_year=STEPS)
        paths = gbm_paths(100.0, p, N_PATHS, STEPS, _rng())
        assert paths.shape == (N_PATHS, STEPS + 1)
        assert paths[:, -1].mean() == pytest.approx(100.0 * np.exp(0.07), rel=0.02)

    def test_realised_volatility_matches_sigma(self):
        p = ProcessParams(mu=0.05, sigma=0.25, steps_per_year=STEPS)
        paths = gbm_paths(100.0, p, N_PATHS, STEPS, _rng(11))
        log_ret = np.diff(np.log(paths), axis=1)
        realised = log_ret.std(ddof=1) * np.sqrt(STEPS)
        assert realised == pytest.approx(0.25, rel=0.03)

    def test_paths_start_at_s0_and_stay_positive(self):
        p = ProcessParams(sigma=0.6, steps_per_year=STEPS)
        paths = gbm_paths(50.0, p, 500, STEPS, _rng())
        assert np.allclose(paths[:, 0], 50.0)
        assert (paths > 0).all(), "a lognormal process can never reach zero"

    def test_same_seed_reproduces_the_path(self):
        p = ProcessParams()
        a = gbm_paths(100.0, p, 64, 50, _rng(123))
        b = gbm_paths(100.0, p, 64, 50, _rng(123))
        assert np.array_equal(a, b)


# ---------------------------------------------------------------- Student-t
class TestStudentT:
    def test_keeps_the_gbm_mean(self):
        """Fat tails must not move the expected return."""
        p = ProcessParams(mu=0.06, sigma=0.20, df=4.0, steps_per_year=STEPS)
        paths = student_t_paths(100.0, p, N_PATHS, STEPS, _rng(3))
        assert paths[:, -1].mean() == pytest.approx(100.0 * np.exp(0.06), rel=0.03)

    def test_tails_are_fatter_than_gaussian(self):
        """Excess kurtosis is the whole point of using t shocks."""
        p = ProcessParams(mu=0.0, sigma=0.20, df=3.5, steps_per_year=STEPS)
        t_paths = student_t_paths(100.0, p, N_PATHS, STEPS, _rng(5))
        g_paths = gbm_paths(100.0, p, N_PATHS, STEPS, _rng(5))

        def kurt(paths):
            r = np.diff(np.log(paths), axis=1).ravel()
            return float(((r - r.mean()) ** 4).mean() / r.var() ** 2)

        assert kurt(t_paths) > kurt(g_paths) + 1.0

    def test_rejects_infinite_variance_df(self):
        with pytest.raises(ValueError, match="df"):
            ProcessParams(df=2.0)


# ------------------------------------------------------------------ Merton
class TestMertonJumpDiffusion:
    def test_compensator_preserves_expected_return(self):
        """With the -lambda*k drift compensator, jumps change risk, not mean."""
        p = ProcessParams(mu=0.07, sigma=0.18, steps_per_year=STEPS,
                          jump_intensity=3.0, jump_mean=-0.05, jump_vol=0.12)
        paths = merton_paths(100.0, p, N_PATHS, STEPS, _rng(17))
        assert paths[:, -1].mean() == pytest.approx(100.0 * np.exp(0.07), rel=0.03)

    def test_jumps_raise_dispersion_at_equal_mean(self):
        base = ProcessParams(mu=0.05, sigma=0.18, steps_per_year=STEPS)
        jumpy = ProcessParams(mu=0.05, sigma=0.18, steps_per_year=STEPS,
                              jump_intensity=6.0, jump_mean=-0.06, jump_vol=0.15)
        q = gbm_paths(100.0, base, N_PATHS, STEPS, _rng(21))
        j = merton_paths(100.0, jumpy, N_PATHS, STEPS, _rng(21))
        # Same expected level, wider distribution and a worse 5% tail.
        assert j[:, -1].mean() == pytest.approx(q[:, -1].mean(), rel=0.05)
        assert j[:, -1].std(ddof=1) > q[:, -1].std(ddof=1)
        assert np.quantile(j[:, -1], 0.05) < np.quantile(q[:, -1], 0.05)

    def test_zero_intensity_reduces_to_gbm(self):
        p = ProcessParams(mu=0.04, sigma=0.2, steps_per_year=STEPS, jump_intensity=0.0)
        m = merton_paths(100.0, p, 2_000, 60, _rng(9))
        # No jumps drawn, so the law matches GBM's mean closely.
        g = gbm_paths(100.0, p, 2_000, 60, _rng(9))
        assert m[:, -1].mean() == pytest.approx(g[:, -1].mean(), rel=0.02)


# ------------------------------------------------------------------ Heston
class TestHeston:
    def test_expected_return_is_preserved(self):
        p = ProcessParams(mu=0.06, steps_per_year=STEPS, kappa=2.0,
                          theta=0.04, xi=0.3, rho=-0.7)
        paths = heston_paths(100.0, p, 8_000, STEPS, _rng(31))
        assert paths[:, -1].mean() == pytest.approx(100.0 * np.exp(0.06), rel=0.05)

    def test_realised_vol_is_near_sqrt_theta(self):
        """Long-run variance theta sets the average volatility level."""
        p = ProcessParams(mu=0.0, steps_per_year=STEPS, kappa=3.0,
                          theta=0.09, xi=0.25, rho=-0.5)
        paths = heston_paths(100.0, p, 6_000, STEPS, _rng(33))
        realised = np.diff(np.log(paths), axis=1).std(ddof=1) * np.sqrt(STEPS)
        assert realised == pytest.approx(np.sqrt(0.09), rel=0.15)

    def test_volatility_clusters(self):
        """Squared returns must be autocorrelated — GBM's cannot be."""
        p = ProcessParams(mu=0.0, steps_per_year=STEPS, kappa=1.0,
                          theta=0.04, xi=0.6, rho=-0.7)
        paths = heston_paths(100.0, p, 400, STEPS * 2, _rng(35))
        r2 = np.diff(np.log(paths), axis=1) ** 2
        # Lag-1 autocorrelation of squared returns, averaged across paths.
        a = r2[:, :-1] - r2[:, :-1].mean(axis=1, keepdims=True)
        b = r2[:, 1:] - r2[:, 1:].mean(axis=1, keepdims=True)
        rho1 = float(np.mean((a * b).mean(axis=1) / (r2.var(axis=1) + 1e-18)))
        assert rho1 > 0.02, "stochastic variance should cluster volatility"


# ----------------------------------------------------------------- Vasicek
class TestVasicek:
    def test_stationary_mean_and_variance(self):
        """Exact OU discretisation: mean -> theta, var -> sigma^2/(2 kappa)."""
        kappa, theta, sigma = 1.5, 0.03, 0.01
        paths = vasicek_paths(0.03, kappa, theta, sigma, 20_000, 2_000, 252, _rng(41))
        tail = paths[:, -1]
        assert tail.mean() == pytest.approx(theta, abs=0.002)
        assert tail.var(ddof=1) == pytest.approx(sigma**2 / (2 * kappa), rel=0.10)

    def test_mean_reverts_from_a_shocked_start(self):
        kappa, theta = 2.0, 0.02
        paths = vasicek_paths(0.10, kappa, theta, 0.005, 2_000, 504, 252, _rng(43))
        # Started far above theta; must decay towards it.
        assert paths[:, -1].mean() < 0.04
        assert paths[:, -1].mean() == pytest.approx(theta, abs=0.005)

    def test_rates_may_go_negative(self):
        """A Gaussian short rate can go below zero — that is Vasicek's nature."""
        paths = vasicek_paths(0.001, 0.5, 0.0, 0.03, 4_000, 252, 252, _rng(45))
        assert (paths < 0).any()

    def test_requires_positive_kappa(self):
        with pytest.raises(ValueError, match="kappa"):
            vasicek_paths(0.02, 0.0, 0.02, 0.01, 10, 10, 252, _rng())


# --------------------------------------------------------------------- CIR
class TestCIR:
    def test_stays_non_negative(self):
        paths = cir_paths(0.02, 1.0, 0.02, 0.15, 4_000, 504, 252, _rng(51))
        assert (paths >= 0).all(), "CIR must never go negative"

    def test_mean_reverts_to_theta(self):
        paths = cir_paths(0.08, 2.5, 0.03, 0.05, 8_000, 1_000, 252, _rng(53))
        assert paths[:, -1].mean() == pytest.approx(0.03, abs=0.006)

    def test_feller_condition_flag(self):
        assert feller_condition(2.0, 0.04, 0.2) is True     # 0.16 >= 0.04
        assert feller_condition(0.1, 0.01, 0.5) is False    # 0.002 < 0.25


# ------------------------------------------------------- news modulation
class TestNewsModulation:
    def test_vol_multiplier_raises_realised_volatility(self):
        p = ProcessParams(mu=0.0, sigma=0.15, steps_per_year=STEPS)
        calm = gbm_paths(100.0, p, 4_000, STEPS, _rng(61))
        storm = gbm_paths(100.0, p, 4_000, STEPS, _rng(61),
                          NewsModulation(vol_multiplier=np.full(STEPS, 3.0)))
        rv = lambda x: np.diff(np.log(x), axis=1).std(ddof=1)
        assert rv(storm) == pytest.approx(3.0 * rv(calm), rel=0.05)

    def test_drift_shift_moves_the_mean(self):
        p = ProcessParams(mu=0.0, sigma=0.10, steps_per_year=STEPS)
        flat = gbm_paths(100.0, p, 8_000, STEPS, _rng(63))
        up = gbm_paths(100.0, p, 8_000, STEPS, _rng(63),
                       NewsModulation(drift_shift=np.full(STEPS, 0.10)))
        assert up[:, -1].mean() > flat[:, -1].mean() * 1.08

    def test_intensity_shift_adds_jumps(self):
        p = ProcessParams(mu=0.0, sigma=0.12, steps_per_year=STEPS,
                          jump_intensity=0.0, jump_mean=-0.08, jump_vol=0.10)
        quiet = merton_paths(100.0, p, 6_000, STEPS, _rng(65))
        newsy = merton_paths(100.0, p, 6_000, STEPS, _rng(65),
                             NewsModulation(intensity_shift=np.full(STEPS, 20.0)))
        assert newsy[:, -1].std(ddof=1) > quiet[:, -1].std(ddof=1)

    def test_scalar_and_resampled_modulation_are_accepted(self):
        m = NewsModulation(vol_multiplier=np.array([2.0]))
        d, v, lam = m.resolve(10)
        assert v.shape == (10,) and np.allclose(v, 2.0)
        coarse = NewsModulation(drift_shift=np.array([0.0, 1.0]))
        d2, _, _ = coarse.resolve(4)
        assert d2.shape == (4,) and d2[0] == 0.0 and d2[-1] == 1.0

    def test_negative_vol_multiplier_is_clipped(self):
        d, v, lam = NewsModulation(vol_multiplier=np.array([-5.0])).resolve(3)
        assert (v >= 0).all()


# ------------------------------------------------------------- registry
class TestRegistryAndStats:
    @pytest.mark.parametrize("name", sorted(PROCESSES))
    def test_every_registered_process_simulates(self, name):
        p = ProcessParams(steps_per_year=STEPS, jump_intensity=1.0)
        paths = simulate_paths(name, 100.0, p, 200, 60, _rng(71))
        assert paths.shape == (200, 61)
        assert np.isfinite(paths).all() and (paths > 0).all()

    def test_unknown_process_raises(self):
        with pytest.raises(KeyError, match="unknown process"):
            simulate_paths("bachelier", 100.0, ProcessParams(), 5, 5, _rng())

    def test_terminal_statistics_reports_risk_tails(self):
        p = ProcessParams(mu=0.05, sigma=0.3, steps_per_year=STEPS)
        stats = terminal_statistics(gbm_paths(100.0, p, 5_000, STEPS, _rng(73)))
        assert stats["paths"] == 5_000
        # Lognormal: mean sits above the median.
        assert stats["mean_return"] > stats["median_return"]
        # Expected shortfall is at least as severe as VaR, by construction.
        assert stats["expected_shortfall_95"] <= stats["var_95"]
        assert stats["worst_return"] <= stats["var_95"]
        assert 0.0 <= stats["prob_loss"] <= 1.0
        assert stats["max_drawdown"] <= 0.0
