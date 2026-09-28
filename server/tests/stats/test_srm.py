"""srm_check against SciPy and a textbook example."""

import numpy as np
import pytest
import scipy.stats

from abtest.stats import srm_check


def test_matches_textbook_example() -> None:
    """Mendel's peas: 315 round-yellow, 108 round-green, 101 wrinkled-yellow, 32 wrinkled-green
    against the 9:3:3:1 ratio. The classic result is chi-square = 0.470 on 3 df, p = 0.925."""
    result = srm_check([315, 108, 101, 32], [9, 3, 3, 1])

    assert result.p_value == pytest.approx(0.9254, abs=5e-5)
    assert not result.flagged


@pytest.mark.parametrize("seed", range(10))
def test_matches_scipy_chisquare(seed: int) -> None:
    rng = np.random.default_rng(seed)
    weights = [5000, 5000] if seed % 2 else [3300, 3300, 3400]
    counts = [
        int(c) for c in rng.multinomial(int(rng.integers(100, 100_000)), np.divide(weights, 10_000))
    ]

    result = srm_check(counts, weights)

    expected = np.sum(counts) * np.divide(weights, np.sum(weights))
    assert result.p_value == pytest.approx(scipy.stats.chisquare(counts, expected).pvalue, rel=1e-9)


def test_flags_a_real_mismatch() -> None:
    # 50/50 split, but treatment lost about 5% of its users:
    # chi-square = (250^2 + 250^2) / 10,000 = 12.5 on 1 df, p = 0.0004.
    result = srm_check([10_250, 9_750], [5000, 5000])

    assert result.p_value is not None and result.p_value < 0.001
    assert result.flagged


def test_does_not_flag_a_split_that_matches_the_weights() -> None:
    assert not srm_check([3_310, 3_290, 3_400], [3300, 3300, 3400]).flagged


def test_skips_when_an_expected_count_is_below_five() -> None:
    # 9 users on a 50/50 split: 4.5 expected per variant.
    result = srm_check([9, 0], [5000, 5000])

    assert result.p_value is None
    assert not result.flagged
    assert result.insufficient_data is not None


@pytest.mark.parametrize(
    ("counts", "weights"),
    [([10, 10], [5000]), ([10], [10_000]), ([10, -1], [5000, 5000]), ([10, 10], [5000, 0])],
)
def test_rejects_invalid_input(counts: list[int], weights: list[float]) -> None:
    with pytest.raises(ValueError):
        srm_check(counts, weights)
