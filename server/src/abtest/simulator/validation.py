"""Monte Carlo validation: simulate many experiments whose truth we know, analyze each with
the real stats engine, and measure how often the engine is wrong.

Data generation is vectorized NumPy. Users arrive in blocks, each user independently lands in
control or treatment with probability 1/2 (as with hashing), and converts with its variant's
rate, so each block's counts are binomial draws. That makes a simulated experiment cost a
few array operations, however many users it has. The analysis then calls the engine once per
experiment per look, exactly as the worker will.
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.stats import binomtest

from abtest.stats import (
    MSPRT,
    ComparisonResult,
    MeanSummary,
    ProportionSummary,
    TwoProportionZTest,
    Verdict,
    WelchTTest,
    power_two_proportions,
    sample_size_two_proportions,
    srm_check,
    verdict,
)

type Counts = NDArray[np.int64]


@dataclass(frozen=True, slots=True)
class Rate:
    """How often something happened across simulated experiments, with its uncertainty."""

    hits: int
    trials: int

    @property
    def value(self) -> float:
        return self.hits / self.trials

    @property
    def ci(self) -> tuple[float, float]:
        """Exact 95% binomial CI (Clopper & Pearson, Biometrika 1934), via SciPy."""
        interval = binomtest(self.hits, self.trials).proportion_ci(0.95, method="exact")
        return float(interval.low), float(interval.high)


@dataclass(frozen=True, slots=True)
class Arrivals:
    """Cumulative per-variant counts at each look, shape (experiments, looks)."""

    users_c: Counts
    users_t: Counts
    conversions_c: Counts
    conversions_t: Counts


def simulate_arrivals(
    rng: np.random.Generator,
    experiments: int,
    looks: int,
    users_per_look: int,
    rate_c: float,
    rate_t: float,
) -> Arrivals:
    """Simulate `looks` blocks of `users_per_look` users (both variants together) per experiment."""
    shape = (experiments, looks)
    new_c = rng.binomial(users_per_look, 0.5, size=shape)
    new_t = users_per_look - new_c
    return Arrivals(
        users_c=new_c.cumsum(axis=1),
        users_t=new_t.cumsum(axis=1),
        conversions_c=rng.binomial(new_c, rate_c).cumsum(axis=1),
        conversions_t=rng.binomial(new_t, rate_t).cumsum(axis=1),
    )


def summaries_at(
    arrivals: Arrivals, experiment: int, look: int
) -> tuple[ProportionSummary, ProportionSummary]:
    """Control and treatment summaries for one experiment at one look."""
    return (
        ProportionSummary(
            int(arrivals.users_c[experiment, look]), int(arrivals.conversions_c[experiment, look])
        ),
        ProportionSummary(
            int(arrivals.users_t[experiment, look]), int(arrivals.conversions_t[experiment, look])
        ),
    )


def is_win(result: ComparisonResult) -> bool:
    """The engine's verdict is a significant improvement (for a metric that should increase)."""
    return verdict(result, "increase", srm_flagged=False) == Verdict.SIGNIFICANT_WIN


# --- Scenarios 1-3: A/A tests (no true effect), fixed, peeking, and sequential -------------


@dataclass(frozen=True, slots=True)
class AAResult:
    """False-positive rates on the same A/A experiments, analyzed three ways.

    `peeking_by_look[k]` and `sequential_by_look[k]` count experiments already declared
    significant at or before look k + 1.
    """

    experiments: int
    looks: int
    users_per_look: int
    baseline: float
    alpha: float
    mde_relative: float
    tau: float
    fixed: Rate
    peeking_by_look: list[Rate]
    sequential_by_look: list[Rate]


def run_aa(
    rng: np.random.Generator,
    experiments: int,
    looks: int = 20,
    users_per_look: int = 1_000,
    baseline: float = 0.10,
    alpha: float = 0.05,
    mde_relative: float = 0.10,
) -> AAResult:
    """A/A experiments: both variants convert at `baseline`, so every "significant" result is
    a false positive.

    - fixed: one z-test at the final look.
    - peeking: a z-test at every look, stopping at the first p < alpha.
    - sequential: the mSPRT at every look, tau = baseline x mde_relative.
    """
    arrivals = simulate_arrivals(rng, experiments, looks, users_per_look, baseline, baseline)
    z_test, msprt = TwoProportionZTest(), MSPRT(tau=baseline * mde_relative)
    fixed_hits = 0
    peeking_first = np.full(experiments, looks)  # look index of the first rejection; looks = never
    sequential_first = np.full(experiments, looks)
    for e in range(experiments):
        state = None
        for k in range(looks):
            control, treatment = summaries_at(arrivals, e, k)
            fixed = z_test.compare(control, treatment, alpha)
            if fixed.significant and peeking_first[e] == looks:
                peeking_first[e] = k
            if k == looks - 1:
                fixed_hits += fixed.significant
            sequential = msprt.compare(control, treatment, alpha, previous=state)
            state = sequential.state
            if sequential.significant and sequential_first[e] == looks:
                sequential_first[e] = k
    return AAResult(
        experiments=experiments,
        looks=looks,
        users_per_look=users_per_look,
        baseline=baseline,
        alpha=alpha,
        mde_relative=mde_relative,
        tau=baseline * mde_relative,
        fixed=Rate(fixed_hits, experiments),
        peeking_by_look=[Rate(int((peeking_first <= k).sum()), experiments) for k in range(looks)],
        sequential_by_look=[
            Rate(int((sequential_first <= k).sum()), experiments) for k in range(looks)
        ],
    )


# --- Scenario 4: power, fixed-horizon vs sequential -----------------------------------------


@dataclass(frozen=True, slots=True)
class PowerCurve:
    """Detection rates for one true lift, at each sample size in `users_per_variant`."""

    lift: float
    n_for_80_percent: int
    users_per_variant: list[int]
    fixed: list[Rate]
    sequential: list[Rate]
    analytic: list[float]


@dataclass(frozen=True, slots=True)
class PowerResult:
    experiments: int
    looks: int
    baseline: float
    alpha: float
    curves: list[PowerCurve]


def run_power(
    rng: np.random.Generator,
    experiments: int,
    lifts: tuple[float, ...] = (0.02, 0.05, 0.10),
    looks: int = 24,
    baseline: float = 0.10,
    alpha: float = 0.05,
) -> PowerResult:
    """How often each analysis detects a real lift (a significant win), by sample size.

    For each lift, the sample sizes run up to 3x the fixed-horizon n for 80% power. At each
    look the fixed-horizon detection rate is "a z-test at exactly this n says win", and the
    sequential rate is "the mSPRT has said win at this look or any earlier one". tau is
    baseline x lift, as if the PM had pre-registered the true lift as the MDE.
    """
    curves = []
    z_test = TwoProportionZTest()
    for lift in lifts:
        n_80 = sample_size_two_proportions(baseline, lift, alpha, power=0.8)
        users_per_look = max(2, round(2 * 3 * n_80 / looks))  # both variants together
        arrivals = simulate_arrivals(
            rng, experiments, looks, users_per_look, baseline, baseline * (1 + lift)
        )
        msprt = MSPRT(tau=baseline * lift)
        fixed_hits = np.zeros(looks, dtype=np.int64)
        sequential_hits = np.zeros(looks, dtype=np.int64)
        for e in range(experiments):
            state, detected = None, False
            for k in range(looks):
                control, treatment = summaries_at(arrivals, e, k)
                fixed_hits[k] += is_win(z_test.compare(control, treatment, alpha))
                sequential = msprt.compare(control, treatment, alpha, previous=state)
                state = sequential.state
                detected = detected or is_win(sequential)
                sequential_hits[k] += detected
        users_per_variant = [users_per_look * (k + 1) // 2 for k in range(looks)]
        curves.append(
            PowerCurve(
                lift=lift,
                n_for_80_percent=n_80,
                users_per_variant=users_per_variant,
                fixed=[Rate(int(h), experiments) for h in fixed_hits],
                sequential=[Rate(int(h), experiments) for h in sequential_hits],
                analytic=[
                    power_two_proportions(baseline, lift, alpha, n) for n in users_per_variant
                ],
            )
        )
    return PowerResult(
        experiments=experiments, looks=looks, baseline=baseline, alpha=alpha, curves=curves
    )


# --- Scenario 5: sample ratio mismatch -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SRMScenarioResult:
    """Detection of a real SRM bug by sample size, and false alarms on healthy experiments.

    `false_alarm_single_look`: flagged at the final look.
    `false_alarm_any_look`: flagged at any of the looks, as a dashboard refreshing on a
    schedule would be.
    """

    experiments: int
    drop_rate: float
    total_users: list[int]
    detection: list[Rate]
    looks: int
    false_alarm_single_look: Rate
    false_alarm_any_look: Rate


def logged_users(
    rng: np.random.Generator, experiments: int, looks: int, users_per_look: int, drop_rate: float
) -> tuple[Counts, Counts]:
    """Cumulative control and treatment users whose exposure was logged, per look.

    Assignment is 50/50; a bug loses each treatment exposure with probability drop_rate.
    """
    new_c = rng.binomial(users_per_look, 0.5, size=(experiments, looks))
    new_t = rng.binomial(users_per_look - new_c, 1 - drop_rate)
    return new_c.cumsum(axis=1), new_t.cumsum(axis=1)


def srm_flags(users_c: Counts, users_t: Counts) -> NDArray[np.bool_]:
    """srm_check on a 50/50 split at every (experiment, look)."""
    flags = np.zeros(users_c.shape, dtype=np.bool_)
    for (e, k), control in np.ndenumerate(users_c):
        flags[e, k] = srm_check([int(control), int(users_t[e, k])], [5000, 5000]).flagged
    return flags


def run_srm(
    rng: np.random.Generator,
    experiments: int,
    healthy_experiments: int,
    drop_rate: float = 0.02,
    looks: int = 20,
) -> SRMScenarioResult:
    """A bug drops `drop_rate` of treatment exposures. How soon does srm_check notice, and how
    often does it cry wolf on healthy 50/50 experiments?

    Detection is measured at 20 sample sizes from 20,000 to 400,000 users. (At p < 0.001, a 2%
    drop needs roughly 170,000 users for 80% detection.) False alarms are measured every
    1,000 users up to 20,000, the A/A scenarios' schedule.
    """
    detection_step = 20_000
    buggy = srm_flags(*logged_users(rng, experiments, looks, detection_step, drop_rate))
    healthy = srm_flags(*logged_users(rng, healthy_experiments, looks, 1_000, 0.0))
    return SRMScenarioResult(
        experiments=experiments,
        drop_rate=drop_rate,
        total_users=[detection_step * (k + 1) for k in range(looks)],
        detection=[Rate(int(buggy[:, k].sum()), experiments) for k in range(looks)],
        looks=looks,
        false_alarm_single_look=Rate(int(healthy[:, -1].sum()), healthy_experiments),
        false_alarm_any_look=Rate(int(healthy.any(axis=1).sum()), healthy_experiments),
    )


# --- Scenario 6: Welch on a skewed metric ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class SkewedResult:
    """Welch's false-positive rate on A/A tests of a lognormal metric, by users per variant."""

    experiments: int
    sigma: float
    alpha: float
    users_per_variant: list[int]
    false_positive: list[Rate]


def run_skewed_means(
    rng: np.random.Generator,
    experiments: int,
    users_per_variant: tuple[int, ...] = (10, 30, 100, 300, 1_000, 3_000, 10_000),
    sigma: float = 1.5,
    alpha: float = 0.05,
) -> SkewedResult:
    """A/A tests of a revenue-like metric: per-user values are lognormal(0, sigma) in both
    variants. With sigma = 1.5 the skewness is about 33, so the t-test's normal approximation
    is strained at small n and should recover as n grows.
    """
    welch = WelchTTest()
    rates = []
    chunk = 200  # experiments per batch, to bound memory at large n
    for n in users_per_variant:
        hits = 0
        for start in range(0, experiments, chunk):
            size = min(chunk, experiments - start)
            values = rng.lognormal(0.0, sigma, size=(size, 2, n))
            sums, sums_sq = values.sum(axis=2), (values**2).sum(axis=2)
            for i in range(size):
                result = welch.compare(
                    MeanSummary(n, float(sums[i, 0]), float(sums_sq[i, 0])),
                    MeanSummary(n, float(sums[i, 1]), float(sums_sq[i, 1])),
                    alpha,
                )
                hits += result.significant
        rates.append(Rate(hits, experiments))
    return SkewedResult(
        experiments=experiments,
        sigma=sigma,
        alpha=alpha,
        users_per_variant=list(users_per_variant),
        false_positive=rates,
    )
