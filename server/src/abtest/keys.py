"""API keys: their format, and how they are stored.

A key is its kind's prefix plus random characters. Only the SHA-256 of the full key is
stored, so a database leak doesn't leak working keys. A fast hash is enough because the keys
are long random strings, not guessable passwords.
"""

import hashlib
from typing import Literal

type KeyKind = Literal["client", "server"]

PREFIXES: dict[KeyKind, str] = {"client": "ck_", "server": "sk_"}
# Enough characters to tell keys apart in a list, far too few to use.
DISPLAY_PREFIX_LENGTH = 11
MIN_LENGTH = 32


def hash_key(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


def display_prefix(key: str) -> str:
    return key[:DISPLAY_PREFIX_LENGTH]


def check_key(key: str, kind: KeyKind) -> None:
    """Reject a key that doesn't look like a key of this kind."""
    if not key.startswith(PREFIXES[kind]) or len(key) < MIN_LENGTH:
        raise ValueError(
            f"a {kind} key starts with {PREFIXES[kind]!r} and has at least {MIN_LENGTH} characters"
        )
