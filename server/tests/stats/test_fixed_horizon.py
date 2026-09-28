"""TwoProportionZTest and WelchTTest, cross-checked against statsmodels, SciPy, and textbook
examples."""

import math

import numpy as np
import pytest
import scipy.stats
from numpy.typing import NDArray
from statsmodels.stats.proportion import confint_proportions_2indep, proportions_ztest
from statsmodels.stats.weightstats import CompareMeans, DescrStatsW

from abtest.stats import MeanSummary, ProportionSummary, TwoProportionZTest, WelchTTest

# Tolerance for agreement with an independent implementation: far tighter than any decision
# depends on, loose enough for floating-point differences in the order of operations.
REL = 1e-9
ABS = 1e-12


def summarize(values: NDArray[np.float64]) -> MeanSummary:
    """Reduce per-user values to the sums the worker computes in SQL."""
    return MeanSummary(n=len(values), sum=float(values.sum()), sum_sq=float((values**2).sum()))


def proportion_cases() -> list[tuple[ProportionSummary, ProportionSummary]]:
    """Seeded random (control, treatment) pairs, from 100 to 200,000 users per variant."""
    rng = np.random.default_rng(1)
    cases = []
    for _ in range(40):
        n_c, n_t = (int(n) for n in rng.integers(100, 200_000, size=2))
        rate = rng.uniform(0.05, 0.6)
        lift = rng.uniform(0.8, 1.25)
        control = ProportionSummary(n_c, int(rng.binomial(n_c, rate)))
        treatment = ProportionSummary(n_t, int(rng.binomial(n_t, rate * lift)))
        cases.append((control, treatment))
    return cases


def value_cases() -> list[tuple[NDArray[np.float64], NDArray[np.float64]]]:
    """Seeded random per-user values: normal and skewed (lognormal), small and large n."""
    rng = np.random.default_rng(2)
    cases = []
    for n_c, n_t in [(3, 2), (5, 40), (30, 30), (200, 150), (5_000, 7_000)]:
        cases.append((rng.normal(10, 3, n_c), rng.normal(11, 5, n_t)))
        cases.append((rng.lognormal(0, 1, n_c), rng.lognormal(0.1, 1.5, n_t)))
    return cases


@pytest.mark.parametrize(("control", "treatment"), proportion_cases())
def test_z_test_matches_statsmodels(
    control: ProportionSummary, treatment: ProportionSummary
) -> None:
    result = TwoProportionZTest().compare(control, treatment, alpha=0.05)

    # statsmodels' default z-test uses the pooled standard error.
    _, p_value = proportions_ztest(
        [treatment.successes, control.successes], [treatment.n, control.n]
    )
    # The Wald interval for the difference uses the unpooled standard error.
    ci_low, ci_high = confint_proportions_2indep(
        treatment.successes, treatment.n, control.successes, control.n,
        method="wald", compare="diff", alpha=0.05,
    )  # fmt: skip
    assert result.p_value == pytest.approx(p_value, rel=REL)
    assert result.ci_low == pytest.approx(ci_low, rel=REL, abs=ABS)
    assert result.ci_high == pytest.approx(ci_high, rel=REL, abs=ABS)
    assert result.abs_diff == pytest.approx(treatment.mean - control.mean, rel=REL, abs=ABS)
    assert result.significant == (p_value < 0.05)


def test_z_test_matches_textbook_example() -> None:
    """R. Webb, "Mostly Harmless Statistics", section 9.3: 13 of 200 students late to the first
    class vs 16 of 200 after lunch. The book reports z = -0.5784 and the 95% CI
    -0.015 +/- 0.0508. It prints the interval as (-0.0508, 0.0358): the lower bound is a typo
    for -0.015 - 0.0508 = -0.0658."""
    first_class, after_lunch = ProportionSummary(200, 13), ProportionSummary(200, 16)

    # The book subtracts after-lunch from first-class, so after-lunch plays the control.
    result = TwoProportionZTest().compare(after_lunch, first_class, alpha=0.05)

    assert result.p_value is not None
    abs_z = scipy.stats.norm.isf(result.p_value / 2)  # recover |z| from the two-sided p-value
    assert abs_z == pytest.approx(0.5784, abs=5e-5)
    assert result.abs_diff == pytest.approx(-0.015)
    assert result.ci_low == pytest.approx(-0.0658, abs=5e-5)
    assert result.ci_high == pytest.approx(0.0358, abs=5e-5)
    assert not result.significant


def test_relative_lift_ci_uses_the_delta_method() -> None:
    # Control converts at 10%, treatment at 12%, 1,000 users each. By hand:
    #   Var(p_t) = 0.12 * 0.88 / 1000 = 1.056e-4      Var(p_c) = 0.10 * 0.90 / 1000 = 9e-5
    #   Var(lift) = 1.056e-4 / 0.1^2 + 0.12^2 * 9e-5 / 0.1^4 = 0.01056 + 0.01296 = 0.02352
    #   95% CI = 0.2 +/- 1.959964 * sqrt(0.02352) = 0.2 +/- 0.300584
    result = TwoProportionZTest().compare(
        ProportionSummary(1000, 100), ProportionSummary(1000, 120), alpha=0.05
    )

    assert result.rel_lift == pytest.approx(0.2)
    assert result.rel_ci_low == pytest.approx(0.2 - 0.300584, abs=1e-6)
    assert result.rel_ci_high == pytest.approx(0.2 + 0.300584, abs=1e-6)


@pytest.mark.parametrize(("control_values", "treatment_values"), value_cases())
def test_welch_matches_scipy_and_statsmodels(
    control_values: NDArray[np.float64], treatment_values: NDArray[np.float64]
) -> None:
    result = WelchTTest().compare(summarize(control_values), summarize(treatment_values), 0.05)

    scipy_result = scipy.stats.ttest_ind(treatment_values, control_values, equal_var=False)
    scipy_ci = scipy_result.confidence_interval(confidence_level=0.95)
    compare_means = CompareMeans(DescrStatsW(treatment_values), DescrStatsW(control_values))
    _, sm_p_value, _ = compare_means.ttest_ind(usevar="unequal")
    sm_ci_low, sm_ci_high = compare_means.tconfint_diff(alpha=0.05, usevar="unequal")

    # The sums lose a little precision against the raw values, hence the looser tolerance.
    for p_value in (scipy_result.pvalue, sm_p_value):
        assert result.p_value == pytest.approx(p_value, rel=1e-7)
    for ci_low, ci_high in ((scipy_ci.low, scipy_ci.high), (sm_ci_low, sm_ci_high)):
        assert result.ci_low == pytest.approx(ci_low, rel=1e-7, abs=ABS)
        assert result.ci_high == pytest.approx(ci_high, rel=1e-7, abs=ABS)


def test_welch_matches_textbook_example() -> None:
    """R. Webb, "Mostly Harmless Statistics", section 9.2: monthly household electricity use,
    Sacramento (n=17, mean 596.2353, s 163.2362) vs Portland (n=16, mean 481.5, s 179.3957).
    The book reports t = 1.9179, df = 30.2598, p = 0.0646, and the 90% CI (13.23, 216.24)."""

    def from_mean_and_sd(n: int, mean: float, sd: float) -> MeanSummary:
        return MeanSummary(n=n, sum=n * mean, sum_sq=(n - 1) * sd**2 + n * mean**2)

    sacramento = from_mean_and_sd(17, 596.2353, 163.2362)
    portland = from_mean_and_sd(16, 481.5, 179.3957)

    # The book subtracts Portland from Sacramento, so Portland plays the control.
    result = WelchTTest().compare(portland, sacramento, alpha=0.10)

    assert result.p_value == pytest.approx(0.0646, abs=5e-5)
    assert result.ci_low == pytest.approx(13.23, abs=5e-3)
    assert result.ci_high == pytest.approx(216.24, abs=5e-3)


def test_welch_relative_ci_uses_the_t_critical_value() -> None:
    # A control with zero variance leaves only the treatment term in the delta-method
    # variance, so the relative CI must equal the absolute CI divided by the control mean.
    control = summarize(np.full(50, 10.0))
    treatment = summarize(np.random.default_rng(3).normal(11, 2, 8))

    result = WelchTTest().compare(control, treatment, alpha=0.05)

    assert result.ci_low is not None and result.ci_high is not None
    assert result.rel_ci_low == pytest.approx(result.ci_low / 10.0, rel=REL)
    assert result.rel_ci_high == pytest.approx(result.ci_high / 10.0, rel=REL)


def test_too_few_users_is_insufficient_data() -> None:
    result = TwoProportionZTest().compare(ProportionSummary(1, 1), ProportionSummary(50, 5), 0.05)

    assert result.insufficient_data is not None
    assert result.p_value is None and result.abs_diff is None
    assert not result.significant


@pytest.mark.parametrize("successes", [0, 300])
def test_no_variation_is_insufficient_data(successes: int) -> None:
    # Everybody converted, or nobody did: there is no standard error to divide by.
    both_same = ProportionSummary(300, successes)

    result = TwoProportionZTest().compare(both_same, both_same, alpha=0.05)

    assert result.insufficient_data is not None
    assert result.p_value is None


def test_welch_with_constant_values_is_insufficient_data() -> None:
    result = WelchTTest().compare(summarize(np.zeros(20)), summarize(np.full(20, 5.0)), alpha=0.05)

    assert result.insufficient_data is not None


def test_zero_control_rate_leaves_only_the_relative_lift_undefined() -> None:
    result = TwoProportionZTest().compare(
        ProportionSummary(500, 0), ProportionSummary(500, 10), alpha=0.05
    )

    assert result.p_value is not None and result.ci_low is not None
    assert result.rel_lift is None
    assert result.rel_ci_low is None and result.rel_ci_high is None


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.05, math.nan])
def test_alpha_outside_zero_to_one_is_rejected(alpha: float) -> None:
    with pytest.raises(ValueError, match="alpha"):
        TwoProportionZTest().compare(ProportionSummary(10, 1), ProportionSummary(10, 2), alpha)
