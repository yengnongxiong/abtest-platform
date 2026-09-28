"""Admin: sample-size estimate for a conversion metric (PRD §11)."""

from typing import Annotated

from fastapi import APIRouter, Query

from abtest.api.deps import ServerProject
from abtest.errors import Unprocessable
from abtest.models import SampleSize
from abtest.stats import sample_size_two_proportions

router = APIRouter(prefix="/admin/sample-size", tags=["experiments"])


@router.get("")
def sample_size(
    project: ServerProject,
    baseline: Annotated[float, Query(gt=0, lt=1, description="The control's conversion rate")],
    mde_relative: Annotated[float, Query(gt=0, description="Smallest lift worth detecting")],
    alpha: Annotated[float, Query(gt=0, lt=1)] = 0.05,
    power: Annotated[float, Query(gt=0, lt=1)] = 0.8,
) -> SampleSize:
    """Users needed per variant for a two-sided, fixed-horizon test. Defined for conversion
    metrics only: the formula is for proportions."""
    try:
        n = sample_size_two_proportions(baseline, mde_relative, alpha, power)
    except ValueError as error:  # e.g. baseline x (1 + MDE) is not a rate below 1
        raise Unprocessable("invalid_design", str(error)) from None
    return SampleSize(users_per_variant=n)
