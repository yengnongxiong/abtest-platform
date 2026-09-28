"""After migrating: make a fresh database usable with no manual steps (PRD §10).

Creates the default project if there is none, registers the client and server keys supplied
through the environment (stored hashed, like any key), and creates the event partitions, so
events have somewhere to go before the worker's first run. Safe to run any number of times.
"""

import psycopg

from abtest.keys import check_key, display_prefix, hash_key

DEFAULT_PROJECT_NAME = "Default project"
LOCK_ID = 7_300_002
PARTITION_DAYS_AHEAD = 14


def bootstrap(conn: psycopg.Connection, client_key: str, server_key: str) -> None:
    check_key(client_key, "client")
    check_key(server_key, "server")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        row = conn.execute("SELECT id FROM projects ORDER BY created_at LIMIT 1").fetchone()
        if row is None:
            row = conn.execute(
                "INSERT INTO projects (name) VALUES (%s) RETURNING id", (DEFAULT_PROJECT_NAME,)
            ).fetchone()
        assert row is not None  # INSERT ... RETURNING always returns the row
        project_id = row[0]
        for kind, key in (("client", client_key), ("server", server_key)):
            # A key that is already registered (to any project) is left alone.
            conn.execute(
                "INSERT INTO api_keys (project_id, kind, key_prefix, key_hash)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (key_hash) DO NOTHING",
                (project_id, kind, display_prefix(key), hash_key(key)),
            )
        conn.execute("SELECT ensure_event_partitions(%s)", (PARTITION_DAYS_AHEAD,))
