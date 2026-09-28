"""How many users an experiment needs before it starts, for a fixed-horizon analysis."""

import math

from scipy.stats import norm


def sample_size_two_proportions(
    baseline: float, mde_relative: float, alpha: float, power: float
) -> int:
    """Users needed per variant to detect a relative lift of mde_relative in a conversion rate.

    Two-sided test at level alpha, with the given power, without continuity correction:

        p1 = baseline,  p2 = baseline * (1 + mde_relative),  p_bar = (p1 + p2) / 2
        n = (z_(1 - alpha/2) sqrt(2 p_bar (1 - p_bar)) + z_power sqrt(p1 (1 - p1) + p2 (1 - p2)))^2
            / (p2 - p1)^2

    rounded up. The first term is the pooled standard error under the null hypothesis, the
    second the unpooled one under the alternative. The z-test is built the same way.

    Source: Fleiss, Levin & Paik, "Statistical Methods for Rates and Proportions", 3rd ed.
    (2003), chapter 4.

    mde_relative is the smallest lift worth detecting, as a positive fraction (0.05 = 5%).
    The test is two-sided, so a drop of that size is also detectable, with nearly the same n.
    """
    if not 0 < baseline < 1:
        raise ValueError(f"baseline must be a rate in (0, 1), got {baseline}")
    if not mde_relative > 0:
        raise ValueError(f"mde_relative must be > 0, got {mde_relative}")
    if not baseline * (1 + mde_relative) < 1:
        raise ValueError("baseline * (1 + mde_relative) must stay below 1")
    if not (0 < alpha < 1 and 0 < power < 1):
        raise ValueError(f"alpha and power must be in (0, 1), got {alpha}, {power}")

    p1 = baseline
    p2 = baseline * (1 + mde_relative)
    p_bar = (p1 + p2) / 2
    z_alpha = float(norm.ppf(1 - alpha / 2))
    z_power = float(norm.ppf(power))
    n = (
        z_alpha * math.sqrt(2 * p_bar * (1 - p_bar))
        + z_power * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    ) ** 2 / (p2 - p1) ** 2
    return math.ceil(n)
