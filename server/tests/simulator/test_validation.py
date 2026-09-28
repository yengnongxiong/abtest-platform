"""Small, fast Monte Carlo calibration checks of the stats engine and the scenarios.

Tolerances come from binomial error, not taste: a simulated rate may miss its expected value
by at most Z standard errors, sqrt(p (1 - p) / experiments). With Z = 3.29 (two-sided 99.9%),
a correct engine would fail a given check for about one seed in a thousand; the seeds are
fixed, so the outcome is deterministic.
"""

import math
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import binom, chi2, ncx2

from abtest.simulator.report import RunInfo, ValidationRun, calibrated_range, write_report
from abtest.simulator.validation import (
    AAResult,
    Rate,
    run_aa,
    run_power,
    run_skewed_means,
    run_srm,
)

Z = 3.29


def tolerance(expected: float, experiments: int) -> float:
    return Z * math.sqrt(expected * (1 - expected) / experiments)


@pytest.fixture(scope="module")
def aa() -> AAResult:
    return run_aa(np.random.default_rng(1), experiments=2_000, looks=10)


def test_fixed_horizon_false_positive_rate_is_alpha(aa: AAResult) -> None:
    assert abs(aa.fixed.value - 0.05) <= tolerance(0.05, aa.experiments)


def test_naive_peeking_inflates_false_positives(aa: AAResult) -> None:
    assert aa.peeking_by_look[-1].value > 0.05 + tolerance(0.05, aa.experiments)


def test_sequential_false_positive_rate_stays_at_most_alpha(aa: AAResult) -> None:
    assert aa.sequential_by_look[-1].value <= 0.05 + tolerance(0.05, aa.experiments)


def test_fixed_horizon_power_matches_theory() -> None:
    # 12 looks spanning 3x the 80%-power sample size, so each look adds a quarter of it per
    # variant: look 4 (index 3) is exactly that size.
    result = run_power(np.random.default_rng(2), experiments=500, lifts=(0.10,), looks=12)
    curve = result.curves[0]

    assert curve.users_per_variant[3] == pytest.approx(curve.n_for_80_percent, abs=1)
    assert abs(curve.fixed[3].value - curve.analytic[3]) <= tolerance(curve.analytic[3], 500)


def test_srm_detection_matches_the_noncentral_chi_square() -> None:
    # Theory: with a fraction d of treatment exposures lost, the logged shares are
    # (1, 1 - d) / (2 - d). The chi-square statistic is then noncentral with
    # lambda = N * sum((share - 1/2)^2 / (1/2)), where N is the number of logged users.
    result = run_srm(np.random.default_rng(3), experiments=500, healthy_experiments=100)
    look = 7  # 160,000 users assigned, where detection is near 80%
    drop = result.drop_rate
    logged = result.total_users[look] * (1 - drop / 2)
    shares = np.array([1, 1 - drop]) / (2 - drop)
    noncentrality = logged * float(np.sum((shares - 0.5) ** 2 / 0.5))
    expected = float(ncx2.sf(chi2.isf(0.001, 1), 1, noncentrality))

    detected = result.detection[look]
    assert abs(detected.value - expected) <= tolerance(expected, detected.trials)


def test_srm_false_alarm_rate_is_the_threshold_at_one_look() -> None:
    result = run_srm(np.random.default_rng(4), experiments=10, healthy_experiments=3_000)

    rate = result.false_alarm_single_look
    assert abs(rate.value - 0.001) <= tolerance(0.001, rate.trials)


def test_welch_is_calibrated_on_a_skewed_metric_at_large_n() -> None:
    result = run_skewed_means(
        np.random.default_rng(5), experiments=1_000, users_per_variant=(10_000,)
    )

    assert abs(result.false_positive[0].value - 0.05) <= tolerance(0.05, 1_000)


def test_calibrated_range_holds_95_percent_of_outcomes() -> None:
    low, high = calibrated_range(20_000, 0.05)

    coverage = binom.cdf(high * 20_000, 20_000, 0.05) - binom.cdf(low * 20_000 - 1, 20_000, 0.05)
    assert low < 0.05 < high
    assert coverage >= 0.95


def test_rate_ci_is_the_exact_binomial_interval() -> None:
    # Clopper-Pearson for 0 of 10 has a closed form: (0, 1 - 0.025^(1/10)).
    low, high = Rate(0, 10).ci

    assert low == 0.0
    assert high == pytest.approx(1 - 0.025 ** (1 / 10))


def test_report_writes_charts_and_summary(tmp_path: Path) -> None:
    rng = np.random.default_rng(6)
    run = ValidationRun(
        aa=run_aa(rng, experiments=50, looks=4),
        power=run_power(rng, experiments=20, looks=4),
        srm=run_srm(rng, experiments=20, healthy_experiments=20, looks=4),
        skewed=run_skewed_means(rng, experiments=20, users_per_variant=(10, 100)),
        info=RunInfo(command="test", seed=6, runtime_seconds=1.0, machine="test"),
    )

    write_report(run, tmp_path)

    for chart in ("aa_false_positives", "power", "srm", "welch_skewed"):
        assert (tmp_path / f"{chart}.png").stat().st_size > 0
    summary = (tmp_path / "summary.md").read_text()
    assert "## Acceptance checks" in summary
    assert summary.count("PASS") + summary.count("FAIL") == 2
