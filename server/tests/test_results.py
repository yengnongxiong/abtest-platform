"""analyze(): aggregates in, a snapshot out, with no database (PRD §14, §15)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from abtest.db.results import (
    ExperimentToAnalyze,
    MetricToAnalyze,
    VariantAggregate,
    VariantToAnalyze,
)
from abtest.models import MSPRTStateOut, SnapshotData
from abtest.results import analyze

CUTOFF = datetime(2026, 9, 28, tzinfo=UTC)
CONTROL = VariantToAnalyze(id=uuid4(), key="control", weight_bp=5000, is_control=True, position=0)
TREATMENT = VariantToAnalyze(
    id=uuid4(), key="treatment", weight_bp=5000, is_control=False, position=1
)


def experiment(analysis_type: str = "sequential") -> ExperimentToAnalyze:
    return ExperimentToAnalyze(
        id=uuid4(), project_id=uuid4(), key="checkout", status="running",
        started_at=CUTOFF, stopped_at=None, analysis_type=analysis_type, alpha=0.05,
        mde_relative=0.1, metrics=[], variants=[TREATMENT, CONTROL],  # out of order on purpose
    )  # fmt: skip


def metric(kind: str = "conversion") -> MetricToAnalyze:
    return MetricToAnalyze.model_validate(
        {
            "id": uuid4(),
            "key": "purchase",
            "kind": kind,
            "direction": "increase",
            "event_name": "purchase",
            "window_hours": 168,
            "expected_baseline": 0.1,
            "role": "primary",
        }
    )


def conversions(
    control: tuple[int, int], treatment: tuple[int, int]
) -> dict[UUID, VariantAggregate]:
    return {
        CONTROL.id: VariantAggregate(users=control[0], converters=control[1], total=0, total_sq=0),
        TREATMENT.id: VariantAggregate(
            users=treatment[0], converters=treatment[1], total=0, total_sq=0
        ),
    }


def run(
    aggregates: dict[UUID, VariantAggregate],
    analysis_type: str = "sequential",
    kind: str = "conversion",
    previous: SnapshotData | None = None,
) -> SnapshotData:
    return analyze(experiment(analysis_type), metric(kind), aggregates, 3, 2, previous, CUTOFF)


def test_a_sequential_snapshot_records_tau_the_counts_and_the_state() -> None:
    snapshot = run(conversions((10_000, 1_000), (10_000, 1_150)))

    assert snapshot.tau == pytest.approx(0.1 * 0.1)  # expected baseline x MDE
    assert [v.key for v in snapshot.variants] == ["control", "treatment"]  # by position
    assert (snapshot.users, snapshot.conflicted_users, snapshot.mismatched_users) == (20_000, 3, 2)
    [comparison] = snapshot.comparisons
    assert comparison.variant_key == "treatment"
    assert comparison.rel_lift == pytest.approx(0.15)
    assert comparison.msprt_state is not None
    assert comparison.verdict == "significant_win"


def test_each_look_continues_from_the_previous_state() -> None:
    # A previous look already reached p = 0.001: the always-valid p-value can't go back up,
    # even though this look's data alone would say "no difference".
    previous = run(conversions((100, 10), (100, 10)))
    previous.comparisons[0].msprt_state = MSPRTStateOut(p_value=0.001, ci_low=0.01, ci_high=0.2)

    snapshot = run(conversions((200, 20), (200, 20)), previous=previous)

    assert snapshot.comparisons[0].p_value == 0.001
    assert snapshot.comparisons[0].significant


def test_fixed_horizon_keeps_no_state() -> None:
    snapshot = run(conversions((10_000, 1_000), (10_000, 1_150)), analysis_type="fixed_horizon")

    assert snapshot.tau is None
    assert snapshot.comparisons[0].msprt_state is None
    assert snapshot.comparisons[0].p_value is not None


def test_a_mean_metric_uses_the_sums() -> None:
    aggregates = {
        CONTROL.id: VariantAggregate(users=100, converters=40, total=1_000.0, total_sq=30_000.0),
        TREATMENT.id: VariantAggregate(users=100, converters=45, total=1_200.0, total_sq=40_000.0),
    }

    snapshot = run(aggregates, analysis_type="fixed_horizon", kind="mean")

    assert snapshot.variants[0].conversions is None
    assert snapshot.comparisons[0].abs_diff == pytest.approx(2.0)  # means 10 and 12


def test_srm_overrides_every_verdict() -> None:
    snapshot = run(conversions((12_000, 1_200), (8_000, 1_200)))

    assert snapshot.srm.flagged
    assert snapshot.comparisons[0].verdict == "srm_untrustworthy"


def test_a_variant_with_no_users_yet_is_insufficient_data_not_a_crash() -> None:
    # 4 users against 0: too few to test anything, and too few for the SRM check (under 5
    # expected per variant). With 50 against 0, SRM would rightly be flagged instead.
    snapshot = run({CONTROL.id: VariantAggregate(users=4, converters=1, total=0, total_sq=0)})

    assert snapshot.variants[1].users == 0
    assert snapshot.comparisons[0].verdict == "insufficient_data"
    assert snapshot.srm.insufficient_data is not None
