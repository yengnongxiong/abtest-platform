"""ProportionSummary and MeanSummary: the numbers every test builds on."""

import numpy as np
import pytest

from abtest.stats import MeanSummary, ProportionSummary


def test_proportion_mean_and_variance() -> None:
    summary = ProportionSummary(n=200, successes=50)

    assert summary.mean == 0.25
    assert summary.variance_of_mean == pytest.approx(0.25 * 0.75 / 200)


def test_mean_summary_matches_numpy() -> None:
    values = np.random.default_rng(9).lognormal(3, 1, 1_000)

    summary = MeanSummary(n=len(values), sum=float(values.sum()), sum_sq=float((values**2).sum()))

    assert summary.mean == pytest.approx(values.mean(), rel=1e-12)
    assert summary.variance == pytest.approx(values.var(ddof=1), rel=1e-9)
    assert summary.variance_of_mean == pytest.approx(values.var(ddof=1) / len(values), rel=1e-9)


def test_rounding_never_makes_the_variance_negative() -> None:
    # Seven users who all had 1.1: the exact variance is 0, but 1.1 isn't exact in binary.
    n, total, total_sq = 7, sum([1.1] * 7), sum([1.1**2] * 7)
    assert total_sq - total**2 / n < 0  # the unclamped formula really does go negative here

    assert MeanSummary(n=n, sum=total, sum_sq=total_sq).variance == 0.0


@pytest.mark.parametrize(("n", "successes"), [(10, 11), (10, -1), (-1, 0)])
def test_proportion_rejects_impossible_counts(n: int, successes: int) -> None:
    with pytest.raises(ValueError):
        ProportionSummary(n=n, successes=successes)


@pytest.mark.parametrize(("n", "sum_sq"), [(-1, 0.0), (5, -1.0)])
def test_mean_summary_rejects_impossible_sums(n: int, sum_sq: float) -> None:
    with pytest.raises(ValueError):
        MeanSummary(n=n, sum=0.0, sum_sq=sum_sq)
