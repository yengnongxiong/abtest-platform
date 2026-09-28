"""Deterministic assignment of users to experiments, variants, and flags (PRD §9).

The SDK assigns users locally, with no network call. The server recomputes the same answer
at ingestion to spot SDK bugs, so this module and sdk-js must agree byte for byte. Both are
tested against shared/hash_test_vectors.json.

- hash: MurmurHash3 x86_32, seed 0, over the UTF-8 bytes, as an unsigned 32-bit integer.
- bucket: hash mod 10,000, a position in basis points.
- Inclusion and variant choice use two independent hashes (different inputs), so raising an
  experiment's traffic adds new users without moving anyone already assigned.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import mmh3

BUCKETS = 10_000  # basis points: weights and traffic are in units of 0.01%


@dataclass(frozen=True, slots=True)
class WeightedVariant:
    key: str
    weight_bp: int
    position: int


def hash32(text: str) -> int:
    """MurmurHash3 x86_32 of the UTF-8 bytes, seed 0, unsigned.

    Raises UnicodeEncodeError for a lone surrogate: it has no UTF-8 encoding, which is why
    the API rejects such user ids (JavaScript would silently replace it, and the two hashes
    would disagree).
    """
    return mmh3.hash(text.encode(), 0, signed=False)


def bucket(text: str) -> int:
    return hash32(text) % BUCKETS


def in_experiment(experiment_key: str, user_id: str, traffic_bp: int) -> bool:
    return bucket(f"{experiment_key}:traffic:{user_id}") < traffic_bp


def choose_variant(experiment_key: str, user_id: str, variants: Sequence[WeightedVariant]) -> str:
    """Walk the variants by position, adding up weights; the user's bucket picks the first
    variant whose running total exceeds it. Weights must sum to 10,000."""
    position = bucket(f"{experiment_key}:variant:{user_id}")
    cumulative = 0
    for variant in sorted(variants, key=lambda v: v.position):
        cumulative += variant.weight_bp
        if position < cumulative:
            return variant.key
    raise ValueError(f"variant weights sum to {cumulative}, not {BUCKETS}")


def assign(
    experiment_key: str, user_id: str, traffic_bp: int, variants: Sequence[WeightedVariant]
) -> str | None:
    """The variant a user sees, or None if the user isn't in the experiment's traffic."""
    if not in_experiment(experiment_key, user_id, traffic_bp):
        return None
    return choose_variant(experiment_key, user_id, variants)


def flag_enabled(flag_key: str, user_id: str, enabled: bool, rollout_bp: int) -> bool:
    return enabled and bucket(f"{flag_key}:rollout:{user_id}") < rollout_bp
