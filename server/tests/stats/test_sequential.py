"""MSPRT: the closed forms against their definitions, hand-worked numbers, and the
always-valid bookkeeping across looks."""

import math
from itertools import pairwise

import numpy as np
import pytest
import scipy.stats
from numpy.typing import NDArray

from abtest.stats import MSPRT, MeanSummary, MSPRTState, ProportionSummary, Summary

REL = 1e-9


def summarize(values: NDArray[np.float64]) -> MeanSummary:
    """Reduce per-user values to the sums the worker computes in SQL."""
    return MeanSummary(n=len(values), sum=float(values.sum()), sum_sq=float((values**2).sum()))


def summary_pairs() -> list[tuple[Summary, Summary]]:
    """Seeded (control, treatment) pairs of both summary types, with a range of effects."""
    rng = np.random.default_rng(4)
    pairs: list[tuple[Summary, Summary]] = []
    for n in (50, 500, 5_000):
        for lift in (1.0, 1.05, 1.3):
            pairs.append(
                (
                    ProportionSummary(n, int(rng.binomial(n, 0.1))),
                    ProportionSummary(n, int(rng.binomial(n, 0.1 * lift))),
                )
            )
            pairs.append((summarize(rng.normal(10, 4, n)), summarize(rng.normal(10 * lift, 4, n))))
    return pairs


def density_ratio(theta: float, v: float, tau: float) -> float:
    """The mixture likelihood ratio from its definition: theta's density when the true
    difference is drawn from N(0, tau^2), i.e. N(0, V + tau^2), over its density when the
    true difference is 0, i.e. N(0, V)."""
    mixture = scipy.stats.norm.pdf(theta, scale=math.sqrt(v + tau**2))
    null = scipy.stats.norm.pdf(theta, scale=math.sqrt(v))
    return float(mixture / null)


@pytest.mark.parametrize(("control", "treatment"), summary_pairs())
def test_first_look_p_value_is_the_inverse_likelihood_ratio(
    control: Summary, treatment: Summary
) -> None:
    tau = 0.2 * control.mean  # a typical choice: baseline x MDE, with MDE = 20%
    result = MSPRT(tau).compare(control, treatment, alpha=0.05)

    theta = treatment.mean - control.mean
    v = control.variance_of_mean + treatment.variance_of_mean
    assert result.p_value == pytest.approx(min(1.0, 1 / density_ratio(theta, v, tau)), rel=REL)


@pytest.mark.parametrize(("control", "treatment"), summary_pairs())
def test_ci_edges_are_where_the_test_starts_rejecting(control: Summary, treatment: Summary) -> None:
    # The CI holds every difference d the test would not reject. At its edges, the likelihood
    # ratio of the data shifted by d must equal exactly 1 / alpha.
    tau, alpha = 0.2 * control.mean, 0.05
    result = MSPRT(tau).compare(control, treatment, alpha)

    theta = treatment.mean - control.mean
    v = control.variance_of_mean + treatment.variance_of_mean
    assert result.ci_low is not None and result.ci_high is not None
    for edge in (result.ci_low, result.ci_high):
        assert density_ratio(theta - edge, v, tau) == pytest.approx(1 / alpha, rel=1e-7)


def test_matches_hand_worked_example() -> None:
    # theta = 0.02, V = 5e-5 + 5e-5 = 1e-4, tau = 0.01 (tau^2 = 1e-4), alpha = 0.05:
    #   Lambda = sqrt(1e-4 / 2e-4) * exp(1e-4 * 0.02^2 / (2 * 1e-4 * 2e-4)) = sqrt(0.5) * e
    #          = 1.922116,  so p = 1 / Lambda = 0.520260
    #   radius = sqrt(1e-4 * 2e-4 / 1e-4 * (2 ln 20 + ln 2)) = sqrt(2e-4 * 6.684612) = 0.036564
    # Each variant: 100 users, sample variance 0.005 (sum_sq = 0.005 * 99 + sum^2 / 100).
    control = MeanSummary(n=100, sum=100.0, sum_sq=0.495 + 100.0)
    treatment = MeanSummary(n=100, sum=102.0, sum_sq=0.495 + 104.04)

    result = MSPRT(tau=0.01).compare(control, treatment, alpha=0.05)

    assert result.abs_diff == pytest.approx(0.02)
    assert result.p_value == pytest.approx(0.520260, abs=1e-6)
    assert result.ci_low == pytest.approx(0.02 - 0.036564, abs=1e-6)
    assert result.ci_high == pytest.approx(0.02 + 0.036564, abs=1e-6)
    assert result.rel_lift == pytest.approx(0.02)  # control mean is 1.0
    assert not result.significant


def run_looks(lift: float, seed: int) -> list[tuple[float | None, float | None, float, bool]]:
    """Look after every 200 users per variant, 25 times.

    Returns (ci_low, ci_high, p_value, significant) for each look.
    """
    rng = np.random.default_rng(seed)
    control = rng.random(5_000) < 0.10
    treatment = rng.random(5_000) < 0.10 * lift
    test, state, looks = MSPRT(tau=0.01), None, []
    for n in range(200, 5_001, 200):
        result = test.compare(
            ProportionSummary(n, int(control[:n].sum())),
            ProportionSummary(n, int(treatment[:n].sum())),
            alpha=0.05,
            previous=state,
        )
        state = result.state
        assert result.p_value is not None
        looks.append((result.ci_low, result.ci_high, result.p_value, result.significant))
    return looks


@pytest.mark.parametrize(("lift", "seed"), [(1.0, 5), (1.0, 6), (1.3, 7), (1.5, 8)])
def test_p_value_never_rises_and_ci_never_widens(lift: float, seed: int) -> None:
    looks = run_looks(lift, seed)

    for (low, high, p, _), (next_low, next_high, next_p, _) in pairwise(looks):
        assert next_p <= p
        if next_low is not None and low is not None and next_high is not None and high is not None:
            assert low <= next_low and next_high <= high


@pytest.mark.parametrize(("lift", "seed"), [(1.0, 5), (1.3, 7), (1.5, 8)])
def test_significant_exactly_when_the_ci_excludes_zero(lift: float, seed: int) -> None:
    for low, high, _, significant in run_looks(lift, seed):
        ci_excludes_zero = low is None or high is None or not low <= 0 <= high
        assert significant == ci_excludes_zero


def test_a_strong_effect_becomes_significant() -> None:
    # Guards against a test that never rejects: a 50% lift must be detected by 5,000 users.
    assert run_looks(1.5, seed=8)[-1][3]


def test_empty_ci_intersection_reports_no_interval() -> None:
    # An earlier look put the difference in [0.5, 0.6]; this look says it's near 0.
    earlier = MSPRTState(p_value=0.01, ci_low=0.5, ci_high=0.6)

    result = MSPRT(tau=0.01).compare(
        ProportionSummary(10_000, 1_000), ProportionSummary(10_000, 1_010), 0.05, earlier
    )

    assert result.ci_low is None and result.ci_high is None
    assert result.rel_ci_low is None and result.rel_ci_high is None
    assert result.p_value == 0.01 and result.significant
    assert result.state is not None and result.state.ci_low > result.state.ci_high


def test_overwhelming_evidence_does_not_overflow() -> None:
    # exp(tau^2 theta^2 / ...) is far beyond a float's range here; the log-space form copes.
    result = MSPRT(tau=0.01).compare(
        ProportionSummary(1_000_000, 100_000), ProportionSummary(1_000_000, 200_000), 0.05
    )

    assert result.p_value == 0.0
    assert result.significant


def test_insufficient_data_passes_the_previous_state_on() -> None:
    earlier = MSPRTState(p_value=0.2, ci_low=-0.1, ci_high=0.3)

    result = MSPRT(tau=0.01).compare(
        ProportionSummary(1, 0), ProportionSummary(40, 4), 0.05, earlier
    )

    assert result.insufficient_data is not None
    assert result.p_value is None
    assert result.state == earlier


def test_no_variation_is_insufficient_data() -> None:
    result = MSPRT(tau=0.01).compare(ProportionSummary(50, 0), ProportionSummary(50, 0), 0.05)

    assert result.insufficient_data is not None
    assert result.state is None


@pytest.mark.parametrize("tau", [0.0, -0.01, math.inf, math.nan])
def test_tau_must_be_positive_and_finite(tau: float) -> None:
    with pytest.raises(ValueError, match="tau"):
        MSPRT(tau)
