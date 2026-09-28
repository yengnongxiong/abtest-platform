"""verdict(): SRM first, then data sufficiency, then significance and the metric's direction."""

import pytest

from abtest.stats import ComparisonResult, Direction, Verdict, verdict


def result(
    abs_diff: float | None,
    significant: bool,
    ci: tuple[float, float] | None = None,
) -> ComparisonResult:
    """A comparison with only the fields verdict() reads filled in."""
    return ComparisonResult(
        abs_diff=abs_diff,
        rel_lift=None,
        ci_low=ci[0] if ci else None,
        ci_high=ci[1] if ci else None,
        rel_ci_low=None,
        rel_ci_high=None,
        p_value=None if abs_diff is None else (0.01 if significant else 0.5),
        significant=significant,
        insufficient_data="too few users" if abs_diff is None else None,
    )


@pytest.mark.parametrize(
    ("comparison", "direction", "expected"),
    [
        (result(0.02, True, (0.01, 0.03)), "increase", Verdict.SIGNIFICANT_WIN),
        (result(0.02, True, (0.01, 0.03)), "decrease", Verdict.SIGNIFICANT_LOSS),
        (result(-0.02, True, (-0.03, -0.01)), "decrease", Verdict.SIGNIFICANT_WIN),
        (result(-0.02, True, (-0.03, -0.01)), "increase", Verdict.SIGNIFICANT_LOSS),
        (result(0.02, False, (-0.01, 0.05)), "increase", Verdict.NOT_SIGNIFICANT),
        (result(None, False), "increase", Verdict.INSUFFICIENT_DATA),
        # mSPRT: the intersected CI is above zero while the latest estimate dipped below.
        (result(-0.001, True, (0.004, 0.02)), "increase", Verdict.SIGNIFICANT_WIN),
        # z-test at the edge: significant by the pooled p-value, unpooled CI touching zero.
        (result(0.02, True, (-0.0001, 0.04)), "increase", Verdict.SIGNIFICANT_WIN),
        # mSPRT with an empty intersected CI: the estimate decides.
        (result(-0.02, True, None), "increase", Verdict.SIGNIFICANT_LOSS),
    ],
)
def test_verdict(comparison: ComparisonResult, direction: Direction, expected: Verdict) -> None:
    assert verdict(comparison, direction, srm_flagged=False) == expected


@pytest.mark.parametrize("comparison", [result(0.02, True, (0.01, 0.03)), result(None, False)])
def test_srm_overrides_everything(comparison: ComparisonResult) -> None:
    assert verdict(comparison, "increase", srm_flagged=True) == Verdict.SRM_UNTRUSTWORTHY
