"""Statistics engine: pure functions and classes, with no database, web, or I/O imports.

The worker (M7) feeds it per-variant summaries and stores what it returns.
"""

from abtest.stats.comparison import ComparisonResult, StatisticalTest
from abtest.stats.fixed_horizon import TwoProportionZTest, WelchTTest
from abtest.stats.sample_size import power_two_proportions, sample_size_two_proportions
from abtest.stats.sequential import MSPRT, MSPRTState, SequentialResult
from abtest.stats.srm import SRMResult, srm_check
from abtest.stats.summaries import MeanSummary, ProportionSummary, Summary
from abtest.stats.verdict import Direction, Verdict, verdict

__all__ = [
    "MSPRT",
    "ComparisonResult",
    "Direction",
    "MSPRTState",
    "MeanSummary",
    "ProportionSummary",
    "SRMResult",
    "SequentialResult",
    "StatisticalTest",
    "Summary",
    "TwoProportionZTest",
    "Verdict",
    "WelchTTest",
    "power_two_proportions",
    "sample_size_two_proportions",
    "srm_check",
    "verdict",
]
