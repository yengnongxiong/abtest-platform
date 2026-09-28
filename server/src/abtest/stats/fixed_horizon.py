"""Fixed-horizon tests: valid when the results are read once, at a sample size chosen upfront.

Each formula is written out here instead of calling SciPy's or statsmodels' test functions,
so the unit tests can cross-check it against those libraries independently. SciPy provides
only the normal and t distribution functions.
"""

import math

from scipy.stats import norm
from scipy.stats import t as t_dist

from abtest.stats.comparison import (
    NO_VARIATION,
    ComparisonResult,
    StatisticalTest,
    check_alpha,
    insufficient,
    relative_lift_delta_method,
    too_few_users,
)
from abtest.stats.summaries import MeanSummary, ProportionSummary


class TwoProportionZTest(StatisticalTest[ProportionSummary]):
    """Two-sided z-test for a difference in conversion rates.

    The p-value uses the pooled standard error, because under the null hypothesis both
    variants share one rate:

        p_pool = (x_c + x_t) / (n_c + n_t)
        z = (p_t - p_c) / sqrt(p_pool (1 - p_pool) (1/n_c + 1/n_t))

    The CI for the difference uses the unpooled (Wald) standard error, because an interval
    shouldn't assume the null is true:

        (p_t - p_c) +/- z_(1 - alpha/2) sqrt(p_c (1 - p_c) / n_c + p_t (1 - p_t) / n_t)

    Source: R. Webb, "Mostly Harmless Statistics" (open textbook, LibreTexts), section 9.3.
    Because the two standard errors differ, the p-value and CI can disagree when the result
    is right at the edge of significance. `significant` follows the p-value.
    """

    def compare(
        self, control: ProportionSummary, treatment: ProportionSummary, alpha: float
    ) -> ComparisonResult:
        check_alpha(alpha)
        if reason := too_few_users(control, treatment):
            return insufficient(reason)

        pooled = (control.successes + treatment.successes) / (control.n + treatment.n)
        pooled_se = math.sqrt(pooled * (1 - pooled) * (1 / control.n + 1 / treatment.n))
        if pooled_se == 0:  # every user converted, or nobody did
            return insufficient(NO_VARIATION)

        diff = treatment.mean - control.mean
        p_value = float(2 * norm.sf(abs(diff / pooled_se)))
        z_crit = float(norm.ppf(1 - alpha / 2))
        margin = z_crit * math.sqrt(control.variance_of_mean + treatment.variance_of_mean)
        rel_lift, rel_low, rel_high = relative_lift_delta_method(control, treatment, z_crit)
        return ComparisonResult(
            abs_diff=diff,
            rel_lift=rel_lift,
            ci_low=diff - margin,
            ci_high=diff + margin,
            rel_ci_low=rel_low,
            rel_ci_high=rel_high,
            p_value=p_value,
            significant=p_value < alpha,
        )


class WelchTTest(StatisticalTest[MeanSummary]):
    """Two-sided Welch t-test for a difference in means, without assuming equal variances.

        se^2 = s_c^2 / n_c + s_t^2 / n_t
        t = (mean_t - mean_c) / se
        df = se^4 / ((s_c^2 / n_c)^2 / (n_c - 1) + (s_t^2 / n_t)^2 / (n_t - 1))

    The df is the Welch-Satterthwaite approximation. The p-value and the CI
    (diff +/- t_(1 - alpha/2, df) * se) both come from the t distribution with that df.

    Sources: B. L. Welch, "The Generalization of 'Student's' Problem when Several Different
    Population Variances are Involved", Biometrika 34 (1947); F. E. Satterthwaite, "An
    Approximate Distribution of Estimates of Variance Components", Biometrics Bulletin 2
    (1946). Worked example: R. Webb, "Mostly Harmless Statistics", section 9.2.
    """

    def compare(
        self, control: MeanSummary, treatment: MeanSummary, alpha: float
    ) -> ComparisonResult:
        check_alpha(alpha)
        if reason := too_few_users(control, treatment):
            return insufficient(reason)

        var_c, var_t = control.variance_of_mean, treatment.variance_of_mean
        se = math.sqrt(var_c + var_t)
        if se == 0:
            return insufficient(NO_VARIATION)

        df = (var_c + var_t) ** 2 / (var_c**2 / (control.n - 1) + var_t**2 / (treatment.n - 1))
        diff = treatment.mean - control.mean
        p_value = float(2 * t_dist.sf(abs(diff / se), df))
        t_crit = float(t_dist.ppf(1 - alpha / 2, df))
        rel_lift, rel_low, rel_high = relative_lift_delta_method(control, treatment, t_crit)
        return ComparisonResult(
            abs_diff=diff,
            rel_lift=rel_lift,
            ci_low=diff - t_crit * se,
            ci_high=diff + t_crit * se,
            rel_ci_low=rel_low,
            rel_ci_high=rel_high,
            p_value=p_value,
            significant=p_value < alpha,
        )
