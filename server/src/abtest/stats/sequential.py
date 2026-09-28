"""Sequential testing with the mixture sequential probability ratio test (mSPRT).

Checking a fixed-horizon test again and again and stopping at the first p < alpha inflates
false positives far above alpha. The mSPRT's p-values and CIs are "always valid": the error
rate stays at most alpha no matter how often, or when, anyone looks.
"""

import math
from dataclasses import asdict, dataclass

from abtest.stats.comparison import (
    NO_VARIATION,
    ComparisonResult,
    StatisticalTest,
    check_alpha,
    insufficient,
    too_few_users,
)
from abtest.stats.summaries import Summary


@dataclass(frozen=True, slots=True)
class MSPRTState:
    """What one look hands to the next: the running-minimum p-value, and the running
    intersection of the confidence intervals (absolute units).

    ci_low > ci_high means the intersection is empty (see MSPRT.compare).
    """

    p_value: float
    ci_low: float
    ci_high: float


@dataclass(frozen=True, slots=True, kw_only=True)
class SequentialResult(ComparisonResult):
    """A ComparisonResult plus the state to pass as `previous` at the next look.

    `state` is None until the first look that has enough data.
    """

    state: MSPRTState | None


class MSPRT(StatisticalTest[Summary]):
    """Always-valid, two-sided test for a difference in means (normal mixing distribution).

    theta is the observed difference (treatment mean - control mean), V its estimated
    variance (Var(mean_t) + Var(mean_c)), and tau^2 the mixing variance. The mixture
    likelihood ratio against "no difference" is

        Lambda = sqrt(V / (V + tau^2)) * exp(tau^2 theta^2 / (2 V (V + tau^2)))

    - Always-valid p-value: p_n = min(p_(n-1), 1 / Lambda_n), starting from 1.
    - Always-valid CI: theta +/- radius, intersected with every earlier CI, where
      radius = sqrt(V (V + tau^2) / tau^2 * (2 ln(1/alpha) + ln((V + tau^2) / V))).
      It holds the differences the test would not reject.

    Source: Johari, Koomen, Pekelis & Walsh, "Peeking at A/B Tests: Why It Matters, and What
    to Do About It", KDD 2017 (normal approximation, with V estimated from the data).

    The p-value and the CI use the same unpooled V, so "significant" and "the CI excludes 0"
    always agree. The fixed-horizon z-test can't promise that, because its p-value uses a
    pooled standard error.
    """

    def __init__(self, tau: float) -> None:
        """tau: the mixing standard deviation, in the metric's units, fixed before the start.

        The error guarantee holds for any fixed tau. tau only changes power: the test is most
        sensitive to effects of roughly tau's size, hence tau = expected baseline x MDE.
        """
        if not (tau > 0 and math.isfinite(tau)):
            raise ValueError(f"tau must be positive and finite, got {tau}")
        self.tau = tau

    def compare(
        self,
        control: Summary,
        treatment: Summary,
        alpha: float,
        previous: MSPRTState | None = None,
    ) -> SequentialResult:
        """One look at the data. Pass the returned `state` back as `previous` at the next look.

        alpha and tau must stay the same for every look at one experiment. If the intersected
        CI becomes empty (the looks disagree about the effect, which has probability at most
        alpha when the model holds), the CI fields are None.
        """
        check_alpha(alpha)
        if reason := too_few_users(control, treatment):
            return _no_new_evidence(reason, previous)
        v = control.variance_of_mean + treatment.variance_of_mean
        if v == 0:
            return _no_new_evidence(NO_VARIATION, previous)

        theta = treatment.mean - control.mean
        tau_sq = self.tau**2
        # Work with log(Lambda): Lambda itself overflows a float once the evidence is strong.
        log_lr = 0.5 * math.log(v / (v + tau_sq)) + tau_sq * theta**2 / (2 * v * (v + tau_sq))
        p_now = min(1.0, math.exp(-log_lr))
        radius = math.sqrt(
            v * (v + tau_sq) / tau_sq * (2 * math.log(1 / alpha) + math.log((v + tau_sq) / v))
        )

        if previous is None:
            state = MSPRTState(p_value=p_now, ci_low=theta - radius, ci_high=theta + radius)
        else:
            state = MSPRTState(
                p_value=min(previous.p_value, p_now),
                ci_low=max(previous.ci_low, theta - radius),
                ci_high=min(previous.ci_high, theta + radius),
            )

        ci_is_empty = state.ci_low > state.ci_high
        ci_low = None if ci_is_empty else state.ci_low
        ci_high = None if ci_is_empty else state.ci_high
        # Relative values divide by the control mean: a plug-in approximation, not a separate
        # always-valid interval for the ratio.
        rel_lift = rel_ci_low = rel_ci_high = None
        if control.mean > 0:
            rel_lift = theta / control.mean
            if ci_low is not None and ci_high is not None:
                rel_ci_low, rel_ci_high = ci_low / control.mean, ci_high / control.mean

        return SequentialResult(
            abs_diff=theta,
            rel_lift=rel_lift,
            ci_low=ci_low,
            ci_high=ci_high,
            rel_ci_low=rel_ci_low,
            rel_ci_high=rel_ci_high,
            p_value=state.p_value,
            significant=state.p_value < alpha,
            state=state,
        )


def _no_new_evidence(reason: str, previous: MSPRTState | None) -> SequentialResult:
    """A look with too little data: report why, and pass the previous state on unchanged."""
    return SequentialResult(**asdict(insufficient(reason)), state=previous)
