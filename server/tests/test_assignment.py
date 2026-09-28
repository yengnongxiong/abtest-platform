"""Assignment (PRD §9): reference hash values, the shared vectors, uniformity, and stickiness."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

import mmh3
import pytest
from scipy.stats import chisquare

from abtest.assignment import (
    WeightedVariant,
    assign,
    bucket,
    choose_variant,
    flag_enabled,
    hash32,
    in_experiment,
)

VECTORS_PATH = Path(__file__).resolve().parents[2] / "shared" / "hash_test_vectors.json"
VECTORS: dict[str, Any] = json.loads(VECTORS_PATH.read_text())

FIFTY_FIFTY = [WeightedVariant("control", 5000, 0), WeightedVariant("treatment", 5000, 1)]
THREE_WAY = [
    WeightedVariant("a", 3300, 0),
    WeightedVariant("b", 3300, 1),
    WeightedVariant("c", 3400, 2),
]


@pytest.mark.parametrize(
    ("data", "seed", "expected"),
    [
        (b"", 0, 0x00000000),
        (b"", 1, 0x514E28B7),
        (b"", 0xFFFFFFFF, 0x81F16F39),
        (b"\0\0\0\0", 0, 0x2362F9DE),
        (b"aaaa", 0x9747B28C, 0x5A97808A),
        (b"Hello, world!", 0x9747B28C, 0x24884CBA),
        (b"The quick brown fox jumps over the lazy dog", 0x9747B28C, 0x2FA826CD),
    ],
)
def test_mmh3_is_murmurhash3_x86_32(data: bytes, seed: int, expected: int) -> None:
    # Published MurmurHash3_x86_32 values (from the SMHasher reference), independent of our
    # own vectors: they confirm the Python side is the right algorithm before the vectors
    # make it the reference for the TypeScript port.
    assert mmh3.hash(data, seed, signed=False) == expected


@pytest.mark.parametrize("vector", VECTORS["hashes"], ids=lambda v: repr(v["input"])[:30])
def test_hash_vectors(vector: dict[str, Any]) -> None:
    assert hash32(vector["input"]) == vector["hash"]
    assert bucket(vector["input"]) == vector["bucket"]


def test_vectors_cover_what_the_prd_asks_for() -> None:
    inputs = [v["input"] for v in VECTORS["hashes"]]
    assert len(inputs) >= 50
    assert "" in inputs
    assert any(len(s) >= 1000 for s in inputs)  # long strings
    assert any(s.isdigit() for s in inputs)  # numeric-looking ids
    assert any(not s.isascii() for s in inputs)  # unicode
    assert any("😀" in s for s in inputs)  # emoji
    assert any(v["hash"] >= 2**31 for v in VECTORS["hashes"])  # the unsigned top bit is used


@pytest.mark.parametrize("vector", VECTORS["assignments"])
def test_assignment_vectors(vector: dict[str, Any]) -> None:
    variants = [WeightedVariant(**v) for v in vector["variants"]]

    got = assign(vector["experiment"], vector["user"], vector["traffic_bp"], variants)

    assert got == vector["variant"]


@pytest.mark.parametrize("vector", VECTORS["flags"])
def test_flag_vectors(vector: dict[str, Any]) -> None:
    got = flag_enabled(vector["flag"], vector["user"], vector["enabled"], vector["rollout_bp"])

    assert got == vector["on"]


@pytest.mark.parametrize(("variants", "name"), [(FIFTY_FIFTY, "50/50"), (THREE_WAY, "33/33/34")])
def test_a_million_users_split_as_weighted(variants: list[WeightedVariant], name: str) -> None:
    counts = Counter(choose_variant("uniformity", f"user-{i}", variants) for i in range(1_000_000))

    observed = [counts[v.key] for v in variants]
    expected = [1_000_000 * v.weight_bp / 10_000 for v in variants]
    assert chisquare(observed, expected).pvalue > 0.001, (name, observed)


def test_traffic_and_variant_hashes_are_independent() -> None:
    # Included users must split like everyone else: if the two hashes were correlated,
    # a 30% rollout could over-represent one variant.
    included = Counter(
        choose_variant("independence", f"user-{i}", FIFTY_FIFTY)
        for i in range(200_000)
        if in_experiment("independence", f"user-{i}", 3_000)
    )

    observed = [included["control"], included["treatment"]]
    assert chisquare(observed).pvalue > 0.001, observed


def test_raising_traffic_never_moves_an_assigned_user() -> None:
    users = [f"user-{i}" for i in range(50_000)]
    before = {u: assign("ramp", u, 2_000, FIFTY_FIFTY) for u in users}
    after = {u: assign("ramp", u, 6_000, FIFTY_FIFTY) for u in users}

    for user, variant in before.items():
        if variant is not None:
            assert after[user] == variant
    assert sum(v is not None for v in after.values()) > sum(v is not None for v in before.values())


def test_variants_are_walked_by_position_not_list_order() -> None:
    listed_backwards = list(reversed(THREE_WAY))

    for i in range(1_000):
        user = f"user-{i}"
        assert choose_variant("order", user, listed_backwards) == choose_variant(
            "order", user, THREE_WAY
        )


def test_weights_that_dont_cover_every_bucket_are_an_error() -> None:
    short = [WeightedVariant("only", 10, 0)]
    user = next(f"user-{i}" for i in range(1_000) if bucket(f"x:variant:user-{i}") >= 10)

    with pytest.raises(ValueError, match="sum to 10"):
        choose_variant("x", user, short)


def test_a_lone_surrogate_cannot_be_hashed() -> None:
    # JavaScript would replace it with U+FFFD and hash something else, so the API rejects
    # such ids instead (PRD §11).
    with pytest.raises(UnicodeEncodeError):
        hash32("user-\ud800")


@pytest.mark.parametrize(
    ("enabled", "rollout_bp", "expected"),
    [(False, 10_000, False), (True, 0, False), (True, 10_000, True)],
)
def test_flag_edges(enabled: bool, rollout_bp: int, expected: bool) -> None:
    assert flag_enabled("flag", "user-1", enabled, rollout_bp) is expected
