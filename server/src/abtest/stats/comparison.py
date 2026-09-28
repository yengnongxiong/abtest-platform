"""The result type and base class shared by every two-sample test."""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from abtest.stats.summaries import Summary

# With zero variance there is no standard error, so no test statistic can be formed.
NO_VARIATION = "no variation: within each variant, every user has the same value"


@dataclass(frozen=True, slots=True, kw_only=True)
class ComparisonResult:
    """Treatment compared with control on one metric.

    Differences are treatment minus control, in the metric's own units. Relative values are
    fractions of the control mean (0.05 means +5%). `significant` means p_value < alpha.

    When the comparison can't be computed, every number is None and `insufficient_data` says
    why. The relative fields alone are None when the control mean isn't positive, because a
    lift relative to 0 is undefined and one relative to a negative baseline has a misleading
    sign.
    """

    abs_diff: float | None
    rel_lift: float | None
    ci_low: float | None
    ci_high: float | None
    rel_ci_low: float | None
    rel_ci_high: float | None
    p_value: float | None
    significant: bool
    insufficient_data: str | None = None


class StatisticalTest[S: Summary](ABC):
    """A two-sided, two-sample test comparing one treatment variant with the control."""

    @abstractmethod
    def compare(self, control: S, treatment: S, alpha: float) -> ComparisonResult:
        """Compare treatment with control at significance level alpha."""


def check_alpha(alpha: float) -> None:
    """Reject a significance level outside (0, 1): that is a caller bug, not a data problem."""
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")


def too_few_users(control: Summary, treatment: Summary) -> str | None:
    """The reason a comparison can't run yet, or None if both variants have >= 2 users.

    A sample variance needs at least two observations.
    """
    if control.n < 2 or treatment.n < 2:
        return f"need at least 2 users per variant (control {control.n}, treatment {treatment.n})"
    return None


def insufficient(reason: str) -> ComparisonResult:
    """A result that carries no numbers, only the reason."""
    return ComparisonResult(
        abs_diff=None,
        rel_lift=None,
        ci_low=None,
        ci_high=None,
        rel_ci_low=None,
        rel_ci_high=None,
        p_value=None,
        significant=False,
        insufficient_data=reason,
    )


def relative_lift_delta_method(
    control: Summary, treatment: Summary, critical_value: float
) -> tuple[float | None, float | None, float | None]:
    """Relative lift (mean_t / mean_c - 1) and its CI: (lift, low, high).

    The ratio of two independent means has no exact standard error, so we use the delta
    method's first-order approximation (Deng, Knoblich & Lu, "Applying the Delta Method in
    Metric Analytics", KDD 2018):

        Var(mean_t / mean_c) ~= Var(mean_t) / mean_c^2 + mean_t^2 * Var(mean_c) / mean_c^4

    The interval is lift +/- critical_value * sqrt(that variance). Callers pass the same
    critical value as their absolute CI, so both intervals use one confidence level.
    All three values are None when the control mean isn't positive.
    """
    mean_c, mean_t = control.mean, treatment.mean
    if mean_c <= 0:
        return None, None, None
    lift = mean_t / mean_c - 1
    variance = treatment.variance_of_mean / mean_c**2 + (
        mean_t**2 * control.variance_of_mean / mean_c**4
    )
    margin = critical_value * math.sqrt(variance)
    return lift, lift - margin, lift + margin
