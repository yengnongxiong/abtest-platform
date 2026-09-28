"""The one-word verdict the dashboard shows for a comparison."""

from enum import StrEnum
from typing import Literal

from abtest.stats.comparison import ComparisonResult

type Direction = Literal["increase", "decrease"]


class Verdict(StrEnum):
    SIGNIFICANT_WIN = "significant_win"
    SIGNIFICANT_LOSS = "significant_loss"
    NOT_SIGNIFICANT = "not_significant"
    SRM_UNTRUSTWORTHY = "srm_untrustworthy"
    INSUFFICIENT_DATA = "insufficient_data"


def verdict(result: ComparisonResult, direction: Direction, srm_flagged: bool) -> Verdict:
    """Classify a comparison, given which direction is good for the metric.

    SRM comes first: a mismatch makes every other number untrustworthy.
    """
    if srm_flagged:
        return Verdict.SRM_UNTRUSTWORTHY
    if result.abs_diff is None:
        return Verdict.INSUFFICIENT_DATA
    if not result.significant:
        return Verdict.NOT_SIGNIFICANT
    went_up = _effect_is_positive(result.abs_diff, result.ci_low, result.ci_high)
    improved = went_up == (direction == "increase")
    return Verdict.SIGNIFICANT_WIN if improved else Verdict.SIGNIFICANT_LOSS


def _effect_is_positive(abs_diff: float, ci_low: float | None, ci_high: float | None) -> bool:
    """Which side of zero a significant effect is on.

    The confidence interval decides when it lies entirely on one side of zero. Under mSPRT
    the interval is intersected across looks, so it can sit above zero while the latest,
    noisier point estimate has dipped below; the interval is the part with the error
    guarantee. Otherwise (e.g. a z-test whose unpooled CI still touches zero), the point
    estimate decides.
    """
    if ci_low is not None and ci_low > 0:
        return True
    if ci_high is not None and ci_high < 0:
        return False
    return abs_diff > 0
