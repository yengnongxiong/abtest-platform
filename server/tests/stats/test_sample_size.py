"""sample_size_two_proportions against statsmodels and a hand-worked example."""

import pytest
from statsmodels.stats.proportion import (
    power_proportions_2indep,
    samplesize_proportions_2indep_onetail,
)

from abtest.stats import power_two_proportions, sample_size_two_proportions


def test_matches_hand_worked_example() -> None:
    # baseline 10%, MDE 10% (so 10% -> 11%), alpha 0.05, power 0.8:
    #   z_0.975 = 1.959964, z_0.8 = 0.841621, p_bar = 0.105
    #   n = (1.959964 sqrt(2 * 0.105 * 0.895) + 0.841621 sqrt(0.1 * 0.9 + 0.11 * 0.89))^2 / 0.01^2
    #     = (0.849706 + 0.364822)^2 / 0.0001 = 14,750.8, rounded up to 14,751
    assert sample_size_two_proportions(0.10, 0.10, alpha=0.05, power=0.8) == 14_751


@pytest.mark.parametrize("baseline", [0.01, 0.05, 0.2, 0.5])
@pytest.mark.parametrize("mde_relative", [0.02, 0.1, 0.5])
@pytest.mark.parametrize(("alpha", "power"), [(0.05, 0.8), (0.01, 0.9)])
def test_matches_statsmodels(
    baseline: float, mde_relative: float, alpha: float, power: float
) -> None:
    n = sample_size_two_proportions(baseline, mde_relative, alpha, power)

    # statsmodels' normal-approximation sample size (pooled SE under the null, unpooled under
    # the alternative) is the same formula, unrounded.
    expected = samplesize_proportions_2indep_onetail(
        diff=baseline * mde_relative, prop2=baseline, power=power, alpha=alpha
    )
    assert 0 <= n - expected < 1


def test_smaller_effects_need_more_users() -> None:
    assert sample_size_two_proportions(0.1, 0.05, 0.05, 0.8) > sample_size_two_proportions(
        0.1, 0.10, 0.05, 0.8
    )


@pytest.mark.parametrize(
    ("baseline", "mde_relative", "alpha", "power"),
    [
        (0.0, 0.1, 0.05, 0.8),  # baseline must be a rate in (0, 1)
        (1.0, 0.1, 0.05, 0.8),
        (0.1, 0.0, 0.05, 0.8),  # the MDE is a positive magnitude
        (0.1, -0.1, 0.05, 0.8),
        (0.6, 1.0, 0.05, 0.8),  # 0.6 * 2 = 1.2 is not a rate
        (0.1, 0.1, 0.0, 0.8),
        (0.1, 0.1, 0.05, 1.0),
    ],
)
def test_rejects_invalid_input(
    baseline: float, mde_relative: float, alpha: float, power: float
) -> None:
    with pytest.raises(ValueError):
        sample_size_two_proportions(baseline, mde_relative, alpha, power)


@pytest.mark.parametrize("baseline", [0.05, 0.2])
@pytest.mark.parametrize("mde_relative", [0.02, 0.1])
@pytest.mark.parametrize("n", [500, 20_000, 400_000])
def test_power_matches_statsmodels(baseline: float, mde_relative: float, n: int) -> None:
    power = power_two_proportions(baseline, mde_relative, alpha=0.05, n_per_variant=n)

    # One tail at alpha/2 is the two-sided test's chance of rejecting in the effect's direction.
    expected = power_proportions_2indep(
        diff=baseline * mde_relative, prop2=baseline, nobs1=n,
        alpha=0.025, alternative="larger", return_results=False,
    )  # fmt: skip
    assert power == pytest.approx(expected, rel=1e-9)


def test_power_is_the_inverse_of_sample_size() -> None:
    n = sample_size_two_proportions(0.10, 0.10, alpha=0.05, power=0.8)

    assert power_two_proportions(0.10, 0.10, alpha=0.05, n_per_variant=n) >= 0.8
    assert power_two_proportions(0.10, 0.10, alpha=0.05, n_per_variant=n - 1) < 0.8
