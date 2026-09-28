"""bootstrap(): a fresh database becomes usable with no manual steps (PRD §10)."""

import hashlib

import psycopg
import pytest

from abtest.db.bootstrap import bootstrap

CLIENT_KEY = "ck_test_" + "a" * 32
SERVER_KEY = "sk_test_" + "b" * 32


def test_creates_the_default_project_and_registers_hashed_keys(conn: psycopg.Connection) -> None:
    bootstrap(conn, CLIENT_KEY, SERVER_KEY)

    projects = conn.execute("SELECT name FROM projects").fetchall()
    keys = conn.execute("SELECT kind, key_prefix, key_hash FROM api_keys ORDER BY kind").fetchall()
    assert projects == [("Default project",)]
    assert keys == [
        ("client", CLIENT_KEY[:11], hashlib.sha256(CLIENT_KEY.encode()).digest()),
        ("server", SERVER_KEY[:11], hashlib.sha256(SERVER_KEY.encode()).digest()),
    ]


def test_never_stores_a_plaintext_key(conn: psycopg.Connection) -> None:
    bootstrap(conn, CLIENT_KEY, SERVER_KEY)

    row = conn.execute("SELECT string_agg(api_keys::text, ' ') FROM api_keys").fetchone()
    assert row is not None
    assert CLIENT_KEY not in row[0] and SERVER_KEY not in row[0]


def test_running_twice_changes_nothing(conn: psycopg.Connection) -> None:
    bootstrap(conn, CLIENT_KEY, SERVER_KEY)
    bootstrap(conn, CLIENT_KEY, SERVER_KEY)

    assert conn.execute("SELECT count(*) FROM projects").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM api_keys").fetchone() == (2,)


def test_uses_an_existing_project(conn: psycopg.Connection) -> None:
    conn.execute("INSERT INTO projects (name) VALUES ('Already here')")

    bootstrap(conn, CLIENT_KEY, SERVER_KEY)

    assert conn.execute("SELECT name FROM projects").fetchall() == [("Already here",)]


def test_creates_the_event_partitions(conn: psycopg.Connection) -> None:
    bootstrap(conn, CLIENT_KEY, SERVER_KEY)

    count = conn.execute(
        "SELECT count(*) FROM pg_inherits WHERE inhparent = 'events'::regclass"
    ).fetchone()
    assert count == (1 + 22,)  # the default partition, and today - 7 through today + 14


@pytest.mark.parametrize(
    ("client_key", "server_key"),
    [
        (SERVER_KEY, SERVER_KEY),  # a server key where the client key belongs
        (CLIENT_KEY, CLIENT_KEY),
        ("ck_short", SERVER_KEY),
    ],
)
def test_rejects_keys_of_the_wrong_kind_or_too_short(
    conn: psycopg.Connection, client_key: str, server_key: str
) -> None:
    with pytest.raises(ValueError, match="key starts with"):
        bootstrap(conn, client_key, server_key)
