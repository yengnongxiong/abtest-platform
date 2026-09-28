"""Sample ratio mismatch (SRM): did each variant get the share of users it was configured for?

A mismatch usually means a bug (a redirect that loses users, a crash in one variant, bot
filtering that hits one arm harder). Its results can't be trusted even when they look
significant.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from scipy.stats import chi2

# Strict on purpose: a flag hides the experiment's results behind a warning, so false alarms
# are costly, while a real SRM bug only grows more obvious as users accumulate.
SRM_P_VALUE_THRESHOLD = 0.001
# Below this expected count per variant the chi-square approximation is unreliable.
MIN_EXPECTED_COUNT = 5


@dataclass(frozen=True, slots=True)
class SRMResult:
    """p_value is None, and flagged False, when the check was skipped for lack of data."""

    p_value: float | None
    flagged: bool
    insufficient_data: str | None = None


def srm_check(observed_counts: Sequence[int], expected_weights: Sequence[float]) -> SRMResult:
    """Pearson's chi-square goodness-of-fit test of observed variant sizes against the weights.

        expected_i = total * weight_i / sum(weights)
        X^2 = sum((observed_i - expected_i)^2 / expected_i),  df = k - 1

    Flags SRM when p < 0.001. Skips the check (returns "insufficient data") when any
    expected count is below 5 (Cochran's rule of thumb).

    Sources: K. Pearson, "On the Criterion that a Given System of Deviations ...", Philosophical
    Magazine 50 (1900); W. G. Cochran, "Some Methods for Strengthening the Common chi-square
    Tests", Biometrics 10 (1954); Fabijan et al., "Diagnosing Sample Ratio Mismatch in Online
    Controlled Experiments", KDD 2019 (SRM as a trustworthiness check).
    """
    if len(observed_counts) != len(expected_weights) or len(observed_counts) < 2:
        raise ValueError("need one weight per variant, and at least 2 variants")
    if any(count < 0 for count in observed_counts) or any(w <= 0 for w in expected_weights):
        raise ValueError("counts must be >= 0 and weights > 0")

    total = sum(observed_counts)
    weight_total = sum(expected_weights)
    expected = [total * w / weight_total for w in expected_weights]
    if min(expected) < MIN_EXPECTED_COUNT:
        return SRMResult(
            p_value=None,
            flagged=False,
            insufficient_data=f"an expected variant count is below {MIN_EXPECTED_COUNT}",
        )

    statistic = sum((o - e) ** 2 / e for o, e in zip(observed_counts, expected, strict=True))
    p_value = float(chi2.sf(statistic, len(expected) - 1))
    return SRMResult(p_value=p_value, flagged=p_value < SRM_P_VALUE_THRESHOLD)
