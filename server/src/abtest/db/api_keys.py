"""API keys: authentication lookups and key management (PRD §10, §11)."""

import secrets
from uuid import UUID

import psycopg
from psycopg.rows import class_row

from abtest.errors import Conflict, NotFound
from abtest.keys import PREFIXES, KeyKind, display_prefix, hash_key
from abtest.models import ApiKey, ApiKeyCreated


def project_for_key(conn: psycopg.Connection, key: str, kind: KeyKind) -> UUID | None:
    """The project that owns this active key of this kind, or None.

    Looked up by the key's hash (a unique index). A server key never works as a client key
    or the other way round, so a server key can't end up in a browser or a URL.
    """
    row = conn.execute(
        "SELECT project_id FROM api_keys WHERE key_hash = %s AND kind = %s AND revoked_at IS NULL",
        (hash_key(key), kind),
    ).fetchone()
    return None if row is None else row[0]


def list_api_keys(conn: psycopg.Connection, project_id: UUID) -> list[ApiKey]:
    with conn.cursor(row_factory=class_row(ApiKey)) as cur:
        return cur.execute(
            "SELECT id, kind, key_prefix, created_at, revoked_at FROM api_keys"
            " WHERE project_id = %s ORDER BY created_at",
            (project_id,),
        ).fetchall()


def create_api_key(conn: psycopg.Connection, project_id: UUID, kind: KeyKind) -> ApiKeyCreated:
    """Generate a key and store only its hash. The plaintext is returned once, here."""
    key = PREFIXES[kind] + secrets.token_urlsafe(32)
    with conn.cursor(row_factory=class_row(ApiKey)) as cur:
        stored = cur.execute(
            "INSERT INTO api_keys (project_id, kind, key_prefix, key_hash)"
            " VALUES (%s, %s, %s, %s) RETURNING id, kind, key_prefix, created_at, revoked_at",
            (project_id, kind, display_prefix(key), hash_key(key)),
        ).fetchone()
    assert stored is not None  # INSERT ... RETURNING returns the row
    return ApiKeyCreated(**stored.model_dump(), key=key)


def revoke_api_key(conn: psycopg.Connection, project_id: UUID, key_id: UUID) -> ApiKey:
    """Revoke a key. Refuses to revoke the project's last active server key, which would
    lock everyone out of the admin API."""
    with conn.transaction():
        # Lock the project's active server keys, so two concurrent revokes can't each see
        # "another server key is still active" and revoke both.
        active_server_keys = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM api_keys WHERE project_id = %s AND kind = 'server'"
                " AND revoked_at IS NULL FOR UPDATE",
                (project_id,),
            )
        ]
        if active_server_keys == [key_id]:
            raise Conflict("last_server_key", "can't revoke the project's last active server key")
        with conn.cursor(row_factory=class_row(ApiKey)) as cur:
            revoked = cur.execute(
                "UPDATE api_keys SET revoked_at = coalesce(revoked_at, now())"
                " WHERE id = %s AND project_id = %s"
                " RETURNING id, kind, key_prefix, created_at, revoked_at",
                (key_id, project_id),
            ).fetchone()
    if revoked is None:
        raise NotFound("api_key_not_found", f"no API key with id {key_id}")
    return revoked
