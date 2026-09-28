"""The attribution query's edge cases, with hand-built fixtures (PRD §13)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from abtest.db.results import ExperimentToAnalyze, aggregate, experiment_to_analyze

STARTED = datetime(2026, 9, 1, tzinfo=UTC)
EXPOSED = STARTED + timedelta(hours=1)  # every user's first exposure
WINDOW = timedelta(hours=24)  # the metric's attribution window
NOW = STARTED + timedelta(days=5)  # the cutoff for a running experiment


@pytest.fixture
def running(conn: psycopg.Connection, project_id: UUID, experiment_id: UUID) -> ExperimentToAnalyze:
    """The test experiment, started at STARTED, with a conversion metric ("purchase", 24 h
    window) and a mean metric ("revenue", 24 h window, over the same event)."""
    for key, kind in (("purchase", "conversion"), ("revenue", "mean")):
        conn.execute(
            "WITH m AS (INSERT INTO metrics"
            "  (project_id, key, name, kind, event_name, direction, window_hours)"
            "  VALUES (%s, %s, %s, %s, 'purchase', 'increase', 24) RETURNING id)"
            " INSERT INTO experiment_metrics (experiment_id, metric_id, role, expected_baseline)"
            " SELECT %s, id, %s, 1 FROM m",
            (project_id, key, key, kind, experiment_id,
             "primary" if key == "purchase" else "secondary"),
        )  # fmt: skip
    conn.execute(
        "UPDATE experiments SET status = 'running', started_at = %s, mde_relative = 0.1"
        " WHERE id = %s",
        (STARTED, experiment_id),
    )
    experiment = experiment_to_analyze(conn, project_id, "checkout-button")
    assert experiment is not None
    return experiment


def expose(
    conn: psycopg.Connection,
    experiment: ExperimentToAnalyze,
    user: str,
    variant: str = "control",
    conflicted: bool = False,
) -> None:
    variant_id = next(v.id for v in experiment.variants if v.key == variant)
    conn.execute(
        "INSERT INTO exposures"
        " (project_id, experiment_id, variant_id, user_id, first_exposed_at, conflicted)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (experiment.project_id, experiment.id, variant_id, user, EXPOSED, conflicted),
    )


def purchase(
    conn: psycopg.Connection,
    experiment: ExperimentToAnalyze,
    user: str,
    at: datetime,
    value: float | None = None,
    name: str = "purchase",
) -> None:
    conn.execute(
        "INSERT INTO events (project_id, event_id, user_id, event_name, occurred_at, value)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (experiment.project_id, uuid4(), user, name, at, value),
    )


def control_row(
    conn: psycopg.Connection, experiment: ExperimentToAnalyze, metric: str = "purchase"
) -> tuple[int, int, float, float]:
    """(users, converters, total, total_sq) for the control, with the cutoff at NOW."""
    chosen = next(m for m in experiment.metrics if m.key == metric)
    rows = aggregate(conn, experiment, chosen, NOW)
    control = next(v.id for v in experiment.variants if v.is_control)
    row = rows[control]
    return row.users, row.converters, row.total, row.total_sq


@pytest.mark.parametrize(
    ("when", "counted"),
    [
        (EXPOSED - timedelta(seconds=1), False),  # before exposure
        (EXPOSED, True),  # exactly at exposure: the window includes its start
        (EXPOSED + WINDOW - timedelta(microseconds=1), True),  # the last instant inside
        (EXPOSED + WINDOW, False),  # exactly at the end: the window excludes it
        (EXPOSED + WINDOW + timedelta(hours=1), False),  # after the window
    ],
)
def test_the_attribution_window(
    conn: psycopg.Connection, running: ExperimentToAnalyze, when: datetime, counted: bool
) -> None:
    expose(conn, running, "user-1")
    purchase(conn, running, "user-1", when)

    assert control_row(conn, running) == (1, int(counted), 0.0, 0.0)


def test_nothing_after_the_cutoff_counts(
    conn: psycopg.Connection, running: ExperimentToAnalyze
) -> None:
    # Inside the window, but after the analysis cutoff (a stopped experiment's stop time).
    expose(conn, running, "user-1")
    purchase(conn, running, "user-1", EXPOSED + timedelta(hours=2))
    metric = next(m for m in running.metrics if m.key == "purchase")

    rows = aggregate(conn, running, metric, cutoff=EXPOSED + timedelta(hours=1))

    assert next(iter(rows.values())).converters == 0


def test_several_conversions_count_once(
    conn: psycopg.Connection, running: ExperimentToAnalyze
) -> None:
    expose(conn, running, "user-1")
    for hour in (2, 3, 4):
        purchase(conn, running, "user-1", EXPOSED + timedelta(hours=hour))

    assert control_row(conn, running) == (1, 1, 0.0, 0.0)


def test_conflicted_users_are_left_out(
    conn: psycopg.Connection, running: ExperimentToAnalyze
) -> None:
    expose(conn, running, "user-1")
    expose(conn, running, "user-2", conflicted=True)
    purchase(conn, running, "user-2", EXPOSED + timedelta(hours=1))

    assert control_row(conn, running) == (1, 0, 0.0, 0.0)


def test_only_the_metrics_event_and_project_count(
    conn: psycopg.Connection, running: ExperimentToAnalyze
) -> None:
    expose(conn, running, "user-1")
    purchase(conn, running, "user-1", EXPOSED + timedelta(hours=1), name="page_view")

    assert control_row(conn, running)[1] == 0


def test_a_mean_metric_sums_per_user_with_zeros_and_nulls(
    conn: psycopg.Connection, running: ExperimentToAnalyze
) -> None:
    # user-1 spends 10 + 5; user-2 buys with no value (counts as 0); user-3 buys nothing.
    for user in ("user-1", "user-2", "user-3"):
        expose(conn, running, user)
    purchase(conn, running, "user-1", EXPOSED + timedelta(hours=1), value=10.0)
    purchase(conn, running, "user-1", EXPOSED + timedelta(hours=2), value=5.0)
    purchase(conn, running, "user-2", EXPOSED + timedelta(hours=1), value=None)

    users, converters, total, total_sq = control_row(conn, running, metric="revenue")

    # Per-user totals are 15, 0, 0: n = 3, sum = 15, sum of squares = 225.
    assert (users, total, total_sq) == (3, 15.0, 225.0)
    assert converters == 2  # users with any purchase event
