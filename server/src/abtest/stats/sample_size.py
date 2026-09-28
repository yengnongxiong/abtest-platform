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
    _check_design(baseline, mde_relative, alpha)
    if not 0 < power < 1:
        raise ValueError(f"power must be in (0, 1), got {power}")

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


def power_two_proportions(
    baseline: float, mde_relative: float, alpha: float, n_per_variant: int
) -> float:
    """Chance that a two-sided z-test at level alpha, with n_per_variant users per variant,
    finds a significant lift when the true rate is baseline * (1 + mde_relative).

    It solves the sample_size_two_proportions formula for the power instead of n:

        power = Phi((|p2 - p1| sqrt(n) - z_(1 - alpha/2) sqrt(2 p_bar (1 - p_bar)))
                    / sqrt(p1 (1 - p1) + p2 (1 - p2)))

    Source: Fleiss, Levin & Paik (2003), chapter 4. Only the tail in the effect's direction
    counts. A "significant" result in the wrong direction is a mistake, not a detection.
    """
    _check_design(baseline, mde_relative, alpha)
    if n_per_variant < 1:
        raise ValueError(f"n_per_variant must be >= 1, got {n_per_variant}")
    p1 = baseline
    p2 = baseline * (1 + mde_relative)
    p_bar = (p1 + p2) / 2
    z_alpha = float(norm.ppf(1 - alpha / 2))
    z_power = (
        abs(p2 - p1) * math.sqrt(n_per_variant) - z_alpha * math.sqrt(2 * p_bar * (1 - p_bar))
    ) / math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    return float(norm.cdf(z_power))


def _check_design(baseline: float, mde_relative: float, alpha: float) -> None:
    """Reject inputs that don't describe a conversion-rate experiment."""
    if not 0 < baseline < 1:
        raise ValueError(f"baseline must be a rate in (0, 1), got {baseline}")
    if not mde_relative > 0:
        raise ValueError(f"mde_relative must be > 0, got {mde_relative}")
    if not baseline * (1 + mde_relative) < 1:
        raise ValueError("baseline * (1 + mde_relative) must stay below 1")
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
