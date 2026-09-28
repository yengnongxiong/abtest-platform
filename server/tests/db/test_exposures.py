"""upsert_exposures(): the earliest exposure wins, and variant conflicts are flagged (PRD §10)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
import pytest

from abtest.db.exposures import Exposure, upsert_exposures

T0 = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def exposure(project_id: UUID, experiment_id: UUID, variants: dict[str, UUID]) -> Exposure:
    """user-1 sees the control at T0, with a matching server-side assignment."""
    return Exposure(
        project_id=project_id,
        experiment_id=experiment_id,
        variant_id=variants["control"],
        user_id="user-1",
        exposed_at=T0,
        assignment_mismatch=False,
    )


def stored(conn: psycopg.Connection) -> list[tuple[str, datetime, bool, bool]]:
    rows = conn.execute(
        "SELECT v.key, e.first_exposed_at, e.conflicted, e.assignment_mismatch"
        " FROM exposures e JOIN variants v ON v.id = e.variant_id ORDER BY e.user_id"
    ).fetchall()
    return [
        (key, exposed_at, conflicted, mismatch) for key, exposed_at, conflicted, mismatch in rows
    ]


def test_records_a_first_exposure(conn: psycopg.Connection, exposure: Exposure) -> None:
    upsert_exposures(conn, [exposure])

    assert stored(conn) == [("control", T0, False, False)]


def test_a_later_exposure_keeps_the_first_time(
    conn: psycopg.Connection, exposure: Exposure
) -> None:
    upsert_exposures(conn, [exposure])
    upsert_exposures(conn, [replace(exposure, exposed_at=T0 + timedelta(hours=1))])

    assert stored(conn) == [("control", T0, False, False)]


def test_an_earlier_exposure_arriving_late_moves_the_first_time_back(
    conn: psycopg.Connection, exposure: Exposure
) -> None:
    # Batches can arrive out of order (a retried beacon, a device that was offline).
    upsert_exposures(conn, [exposure])
    upsert_exposures(conn, [replace(exposure, exposed_at=T0 - timedelta(hours=1))])

    assert stored(conn) == [("control", T0 - timedelta(hours=1), False, False)]


def test_a_second_variant_marks_the_user_conflicted(
    conn: psycopg.Connection, exposure: Exposure, variants: dict[str, UUID]
) -> None:
    upsert_exposures(conn, [exposure])
    upsert_exposures(conn, [replace(exposure, variant_id=variants["treatment"])])

    assert stored(conn) == [("control", T0, True, False)]


def test_duplicates_within_one_batch_are_merged_first(
    conn: psycopg.Connection, exposure: Exposure, variants: dict[str, UUID]
) -> None:
    # Without the GROUP BY, Postgres would reject the statement: ON CONFLICT DO UPDATE
    # can't touch the same row twice.
    batch = [
        replace(exposure, exposed_at=T0 + timedelta(minutes=5), variant_id=variants["treatment"]),
        exposure,
        replace(exposure, exposed_at=T0 + timedelta(minutes=9)),
    ]

    upsert_exposures(conn, batch)

    # The variant seen first (the control at T0) is kept, and the user is conflicted.
    assert stored(conn) == [("control", T0, True, False)]


def test_an_assignment_mismatch_is_never_forgotten(
    conn: psycopg.Connection, exposure: Exposure
) -> None:
    upsert_exposures(conn, [replace(exposure, assignment_mismatch=True)])
    upsert_exposures(conn, [replace(exposure, exposed_at=T0 + timedelta(hours=1))])

    assert stored(conn) == [("control", T0, False, True)]


def test_users_are_tracked_separately(conn: psycopg.Connection, exposure: Exposure) -> None:
    upsert_exposures(conn, [exposure, replace(exposure, user_id="user-2")])

    assert len(stored(conn)) == 2


def test_an_empty_batch_is_a_no_op(conn: psycopg.Connection) -> None:
    upsert_exposures(conn, [])

    assert stored(conn) == []
