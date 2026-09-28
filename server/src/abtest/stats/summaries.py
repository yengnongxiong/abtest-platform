"""Per-variant summaries: the only data the statistical tests need.

The worker reduces each variant's users to a few numbers in SQL, so the engine never sees
individual rows.
"""

from dataclasses import dataclass
from typing import Protocol


class Summary(Protocol):
    """What every test needs from one variant: its size, its mean, and that mean's variance."""

    @property
    def n(self) -> int: ...

    @property
    def mean(self) -> float: ...

    @property
    def variance_of_mean(self) -> float: ...


@dataclass(frozen=True, slots=True)
class ProportionSummary:
    """A conversion metric for one variant: `successes` of `n` users converted."""

    n: int
    successes: int

    def __post_init__(self) -> None:
        if not 0 <= self.successes <= self.n:
            raise ValueError(
                f"need 0 <= successes <= n, got successes={self.successes}, n={self.n}"
            )

    @property
    def mean(self) -> float:
        """The conversion rate p = successes / n. Requires n >= 1."""
        return self.successes / self.n

    @property
    def variance_of_mean(self) -> float:
        """p(1 - p) / n, the binomial variance of the observed rate (unpooled)."""
        p = self.mean
        return p * (1 - p) / self.n


@dataclass(frozen=True, slots=True)
class MeanSummary:
    """A mean metric for one variant: n users, the sum of their values, and the sum of squares.

    These three numbers are all a Welch test needs, and SQL computes them in one pass.
    """

    n: int
    sum: float
    sum_sq: float

    def __post_init__(self) -> None:
        if self.n < 0 or self.sum_sq < 0:
            raise ValueError(f"need n >= 0 and sum_sq >= 0, got n={self.n}, sum_sq={self.sum_sq}")

    @property
    def mean(self) -> float:
        """The per-user mean. Requires n >= 1."""
        return self.sum / self.n

    @property
    def variance(self) -> float:
        """The sample variance (n - 1 denominator), from the sums. Requires n >= 2.

        The one-pass formula (sum_sq - sum^2 / n) / (n - 1) loses precision when the variance
        is tiny relative to the squared mean (Chan, Golub & LeVeque, "Algorithms for Computing
        the Sample Variance", The American Statistician, 1983). Per-user metric values are not
        in that regime. Rounding can still push a zero variance slightly negative, so the result
        is clamped at 0.
        """
        return max(0.0, (self.sum_sq - self.sum**2 / self.n) / (self.n - 1))

    @property
    def variance_of_mean(self) -> float:
        """s^2 / n, the estimated variance of the sample mean."""
        return self.variance / self.n
