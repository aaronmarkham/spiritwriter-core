"""Tests for canonical forms under symmetry."""

import itertools
import random

import pytest

from spiritwriter.fabric.orbit import (
    anchor_cycle,
    apply_permutation,
    canonical_cycle,
    canonical_under,
    cycle_digest,
    least_rotation,
    orbit_digest,
    permutation_closure,
)
from spiritwriter.fabric.shard import _canonical_json


def _brute_least_rotation(keys):
    """Reference implementation: O(n^2), obviously correct."""
    n = len(keys)
    if n == 0:
        return 0
    rotations = [(list(keys[i:] + keys[:i]), i) for i in range(n)]
    return min(rotations)[1]


def _rotations(seq):
    n = len(seq)
    return [list(seq[i:]) + list(seq[:i]) for i in range(n)]


# ── least_rotation ───────────────────────────────────────────────────


def test_least_rotation_matches_brute_force_random():
    rng = random.Random(20260817)
    for _ in range(400):
        n = rng.randint(1, 9)
        # Small alphabet on purpose: forces repeated elements, which is
        # where naive least-rotation implementations break.
        seq = [rng.choice(["a", "b", "c"]) for _ in range(n)]
        keys = [_canonical_json(x) for x in seq]
        assert least_rotation(keys) == _brute_least_rotation(keys), seq


def test_least_rotation_degenerate_inputs():
    assert least_rotation([]) == 0
    assert least_rotation([_canonical_json("a")]) == 0
    all_same = [_canonical_json("a")] * 5
    assert least_rotation(all_same) == 0


def test_least_rotation_all_identical_elements_is_stable():
    keys = [_canonical_json("x")] * 6
    assert least_rotation(keys) == 0


# ── canonical_cycle: invariance ──────────────────────────────────────


def test_rotation_invariance():
    base = ["alpha", "beta", "gamma", "delta"]
    forms = {canonical_cycle(r) for r in _rotations(base)}
    assert len(forms) == 1


def test_reflection_invariance_when_enabled():
    base = ["alpha", "beta", "gamma", "delta"]
    assert canonical_cycle(base) == canonical_cycle(base[::-1])


def test_reflection_distinguished_when_disabled():
    """Chirality must survive when direction carries meaning."""
    base = ["alpha", "beta", "gamma", "delta"]
    assert canonical_cycle(base, reflect=False) != canonical_cycle(
        base[::-1], reflect=False
    )


def test_rotation_invariance_holds_without_reflection():
    base = ["alpha", "beta", "gamma", "delta"]
    forms = {canonical_cycle(r, reflect=False) for r in _rotations(base)}
    assert len(forms) == 1


def test_idempotent():
    base = ["c", "a", "d", "b"]
    once = canonical_cycle(base)
    assert canonical_cycle(once) == once
    assert canonical_cycle(list(once)) == once


def test_full_dihedral_orbit_collapses_to_one_form():
    """All 2n rotations+reflections of a ring share one canonical form."""
    base = ["w", "x", "y", "z", "q"]
    orbit = _rotations(base) + _rotations(base[::-1])
    assert len({canonical_cycle(m) for m in orbit}) == 1
    assert len(orbit) == 2 * len(base)


# ── canonical_cycle: separation ──────────────────────────────────────


def test_distinct_cycles_stay_distinct():
    """Canonicalization must not over-merge."""
    a = canonical_cycle(["a", "b", "c", "d"])
    b = canonical_cycle(["a", "c", "b", "d"])
    assert a != b


def test_no_false_merges_across_all_small_permutations():
    """Every distinct ring on 5 labels keeps a distinct canonical form.

    Number of distinct canonical forms must equal the number of
    dihedral equivalence classes: (n-1)!/2 for n distinct labels.
    """
    labels = ["a", "b", "c", "d", "e"]
    forms = {canonical_cycle(p) for p in itertools.permutations(labels)}
    expected = 4 * 3 * 2 * 1 // 2  # (n-1)! / 2
    assert len(forms) == expected


def test_multiplicity_matters():
    assert canonical_cycle(["a", "a", "b"]) != canonical_cycle(["a", "b", "b"])


# ── canonical_cycle: degenerate and mixed input ──────────────────────


def test_empty_and_singleton():
    assert canonical_cycle([]) == ()
    assert canonical_cycle(["only"]) == ("only",)


def test_mixed_types_do_not_raise():
    """Total order comes from canonical JSON, not Python's `<`."""
    mixed = [1, "b", None, 2.5, True]
    result = canonical_cycle(mixed)
    assert len(result) == len(mixed)
    assert set(map(repr, result)) == set(map(repr, mixed))


def test_mixed_types_rotation_invariant():
    mixed = [1, "b", None, 2.5]
    forms = {canonical_cycle(r) for r in _rotations(mixed)}
    assert len(forms) == 1


def test_dict_elements_are_supported():
    """Elements need not be hashable; the digest is the hashable handle."""
    ring = [{"id": 2}, {"id": 1}, {"id": 3}]
    assert len({cycle_digest(r) for r in _rotations(ring)}) == 1


def test_returns_hashable_tuple():
    assert isinstance(canonical_cycle(["a", "b"]), tuple)
    {canonical_cycle(["a", "b"]): "usable as a dict key"}


# ── anchor_cycle ─────────────────────────────────────────────────────


def test_anchor_rotates_to_front_and_keeps_direction():
    seq = ["a", "b", "c", "d"]
    assert anchor_cycle(seq, "c") == ("c", "d", "a", "b")


def test_anchor_is_rotation_invariant():
    seq = ["a", "b", "c", "d"]
    assert {anchor_cycle(r, "c") for r in _rotations(seq)} == {
        ("c", "d", "a", "b")
    }


def test_anchor_preserves_chirality():
    seq = ["a", "b", "c", "d"]
    assert anchor_cycle(seq, "a") != anchor_cycle(seq[::-1], "a")


def test_anchor_missing_raises():
    with pytest.raises(ValueError, match="not present"):
        anchor_cycle(["a", "b"], "z")


def test_anchor_ambiguous_raises():
    """Silently picking the first match would be a correctness bug."""
    with pytest.raises(ValueError, match="ambiguous"):
        anchor_cycle(["a", "b", "a"], "a")


# ── cycle_digest ─────────────────────────────────────────────────────


def test_digest_agrees_with_canonical_form():
    base = ["m", "n", "o", "p"]
    digests = {cycle_digest(m) for m in _rotations(base) + _rotations(base[::-1])}
    assert len(digests) == 1


def test_digest_separates_distinct_cycles():
    assert cycle_digest(["a", "b", "c", "d"]) != cycle_digest(
        ["a", "c", "b", "d"]
    )


def test_digest_is_hex_sha256():
    d = cycle_digest(["a", "b", "c"])
    assert len(d) == 64
    assert all(c in "0123456789abcdef" for c in d)


def test_digest_stable_across_calls():
    assert cycle_digest(["a", "b", "c"]) == cycle_digest(["c", "a", "b"])


def test_digest_respects_reflect_flag():
    base = ["a", "b", "c", "d"]
    assert cycle_digest(base, reflect=False) != cycle_digest(
        base[::-1], reflect=False
    )


# ── permutation groups ───────────────────────────────────────────────


def test_apply_permutation_semantics():
    assert apply_permutation(["a", "b", "c"], [2, 0, 1]) == ("c", "a", "b")


def test_apply_permutation_validates_input():
    with pytest.raises(ValueError, match="length"):
        apply_permutation(["a", "b"], [0, 1, 2])
    with pytest.raises(ValueError, match="not a permutation"):
        apply_permutation(["a", "b", "c"], [0, 0, 1])


def test_canonical_under_is_invariant_across_the_group():
    """Every member of the orbit canonicalizes to the same element."""
    # Cyclic group of order 4 acting on 4 slots.
    group = [[(i + s) % 4 for i in range(4)] for s in range(4)]
    items = ["w", "x", "y", "z"]
    forms = {canonical_under(apply_permutation(items, g), group) for g in group}
    assert len(forms) == 1


def test_canonical_under_identity_only_is_a_no_op():
    items = ["c", "a", "b"]
    assert canonical_under(items, []) == tuple(items)
    assert canonical_under(items, [[0, 1, 2]]) == tuple(items)


def test_canonical_under_does_not_over_merge():
    """A group must not collapse structures outside its own orbits."""
    group = [[(i + s) % 4 for i in range(4)] for s in range(4)]
    a = canonical_under(["a", "b", "c", "d"], group)
    b = canonical_under(["a", "c", "b", "d"], group)
    assert a != b


def test_canonical_under_matches_cycle_for_the_rotation_group():
    group = [[(i + s) % 5 for i in range(5)] for s in range(5)]
    items = ["e", "d", "c", "b", "a"]
    assert canonical_under(items, group) == canonical_cycle(items, reflect=False)


def test_orbit_digest_agrees_with_canonical_under():
    group = [[(i + s) % 4 for i in range(4)] for s in range(4)]
    items = ["w", "x", "y", "z"]
    digests = {
        orbit_digest(apply_permutation(items, g), group) for g in group
    }
    assert len(digests) == 1


# ── package surface ──────────────────────────────────────────────────


def test_exported_from_fabric_package():
    """Every fabric submodule re-exports through the package root."""
    import spiritwriter.fabric as fabric

    for name in (
        "least_rotation",
        "canonical_cycle",
        "anchor_cycle",
        "cycle_digest",
        "apply_permutation",
        "permutation_closure",
        "canonical_under",
        "orbit_digest",
    ):
        assert hasattr(fabric, name), name
        assert name in fabric.__all__, name


# ── permutation_closure ──────────────────────────────────────────────


def test_closure_of_one_rotation_generator_is_the_cyclic_group():
    assert permutation_closure([[1, 2, 3, 0]], 4) == (
        (0, 1, 2, 3),
        (1, 2, 3, 0),
        (2, 3, 0, 1),
        (3, 0, 1, 2),
    )


def test_closure_of_nothing_is_the_identity():
    assert permutation_closure([], 3) == ((0, 1, 2),)
    assert permutation_closure([[0, 1, 2]], 3) == ((0, 1, 2),)


def test_closure_is_idempotent():
    once = permutation_closure([[1, 2, 3, 0], [3, 2, 1, 0]], 4)
    assert permutation_closure(once, 4) == once


def test_closure_of_dihedral_generators_has_order_2n():
    group = permutation_closure([[1, 2, 3, 0], [3, 2, 1, 0]], 4)
    assert len(group) == 8


def test_closure_of_transposition_and_cycle_is_the_symmetric_group():
    group = permutation_closure([[1, 0, 2, 3], [1, 2, 3, 0]], 4)
    assert len(group) == 24  # 4!


def test_closure_supplies_inverses():
    """Finite order means composition alone closes the group."""
    group = permutation_closure([[1, 2, 3, 0]], 4)
    assert (3, 0, 1, 2) in group  # inverse of the generator


def test_closure_validates_permutations():
    with pytest.raises(ValueError, match="length"):
        permutation_closure([[0, 1]], 3)
    with pytest.raises(ValueError, match="not a permutation"):
        permutation_closure([[0, 0, 1]], 3)


def test_closure_respects_max_order():
    with pytest.raises(ValueError, match="max_order"):
        permutation_closure([[1, 2, 0]], 3, max_order=2)


# ── canonical_under: generating sets ─────────────────────────────────


def test_generating_set_canonicalizes_the_whole_orbit():
    """A generating set must behave like the group it generates.

    Regression: taking the min over only the supplied permutations sent
    different members of one orbit to different representatives, with
    no error — the exact failure canonicalization exists to prevent.
    """
    gen = [[1, 2, 3, 0]]  # one rotation; generates C4, is not C4
    items = ["b", "a", "c", "d"]
    forms = {canonical_under(rot, gen) for rot in _rotations(items)}
    assert len(forms) == 1


def test_generating_set_matches_the_full_group():
    gen = [[1, 2, 3, 0]]
    full = [[(i + s) % 4 for i in range(4)] for s in range(4)]
    items = ["b", "a", "c", "d"]
    assert canonical_under(items, gen) == canonical_under(items, full)


def test_dihedral_generators_match_canonical_cycle():
    """Rotation + reflection generators reproduce the dihedral form."""
    rotate = [1, 2, 3, 0]
    reflect = [3, 2, 1, 0]
    for items in itertools.permutations(["a", "b", "c", "d"]):
        assert canonical_under(items, [rotate, reflect]) == canonical_cycle(
            items
        )


def test_close_false_skips_closure():
    """The escape hatch does what it says — and why it is a hatch."""
    gen = [[1, 2, 3, 0]]
    items = ["b", "a", "c", "d"]
    forms = {
        canonical_under(rot, gen, close=False) for rot in _rotations(items)
    }
    assert len(forms) > 1  # documents the hazard the default avoids


def test_close_false_agrees_when_the_group_is_already_closed():
    full = [[(i + s) % 4 for i in range(4)] for s in range(4)]
    items = ["b", "a", "c", "d"]
    assert canonical_under(items, full, close=False) == canonical_under(
        items, full
    )


def test_canonical_under_propagates_max_order():
    with pytest.raises(ValueError, match="max_order"):
        canonical_under(["a", "b", "c"], [[1, 2, 0]], max_order=2)


def test_orbit_digest_uses_the_generated_group():
    gen = [[1, 2, 3, 0]]
    items = ["b", "a", "c", "d"]
    assert len({orbit_digest(r, gen) for r in _rotations(items)}) == 1


# ── key= surrogates ──────────────────────────────────────────────────


def test_non_serializable_elements_raise_a_locating_error():
    with pytest.raises(TypeError, match="element at index 1"):
        canonical_cycle(["a", {1, 2}])
    with pytest.raises(TypeError, match="key="):
        canonical_cycle(["a", b"raw"])


def test_key_unblocks_non_serializable_elements():
    from datetime import date

    days = [date(2026, 3, 1), date(2026, 1, 1), date(2026, 2, 1)]
    forms = {
        canonical_cycle(r, key=lambda d: d.isoformat()) for r in _rotations(days)
    }
    assert len(forms) == 1
    assert forms.pop()[0] == date(2026, 1, 1)


def test_key_returns_original_elements_not_surrogates():
    items = [{"id": 3}, {"id": 1}, {"id": 2}]
    form = canonical_cycle(items, key=lambda d: d["id"])
    assert form[0] == {"id": 1}
    assert all(isinstance(x, dict) for x in form)


def test_key_orders_by_surrogate_not_by_element():
    """A key that inverts the order must change which rotation wins."""
    items = [1, 2, 3]
    invert = {1: "c", 2: "b", 3: "a"}
    assert canonical_cycle(items, key=invert.get)[0] == 3
    assert canonical_cycle(items)[0] == 1


def test_ordering_is_lexicographic_over_json_bytes_not_numeric():
    """Documents a real surprise: 10 sorts before 9, as text does.

    Any total order canonicalizes correctly, so this is not a bug — but
    callers who expect numeric ordering should pass a zero-padded or
    otherwise text-sortable key.
    """
    assert canonical_cycle([9, 10, 11], reflect=False)[0] == 10
    padded = canonical_cycle([9, 10, 11], reflect=False, key="{:03d}".format)
    assert padded[0] == 9


def test_key_applies_to_digest():
    items = [{"id": 1, "seen": "monday"}, {"id": 2, "seen": "tuesday"}]
    other = [{"id": 1, "seen": "friday"}, {"id": 2, "seen": "sunday"}]
    by_id = lambda d: d["id"]  # noqa: E731
    assert cycle_digest(items, key=by_id) == cycle_digest(other, key=by_id)
    assert cycle_digest(items) != cycle_digest(other)


def test_key_applies_to_anchor_and_elements():
    from datetime import date

    days = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
    iso = lambda d: d.isoformat()  # noqa: E731
    assert anchor_cycle(days, date(2026, 2, 1), key=iso)[0] == date(2026, 2, 1)


def test_anchor_key_error_names_the_anchor():
    with pytest.raises(TypeError, match="anchor"):
        anchor_cycle(["a", "b"], {1, 2})


def test_key_works_with_permutation_groups():
    from datetime import date

    days = [date(2026, 3, 1), date(2026, 1, 1)]
    assert canonical_under(days, [[1, 0]], key=lambda d: d.isoformat()) == (
        date(2026, 1, 1),
        date(2026, 3, 1),
    )


# ── digest domain separation ─────────────────────────────────────────


def test_cycle_and_orbit_digests_do_not_collide():
    items = ["a", "b", "c"]
    assert cycle_digest(items) != orbit_digest(items, [])


def test_digest_is_not_a_bare_content_hash():
    """A digest means 'canonical form under a symmetry', not 'these bytes'."""
    from spiritwriter.fabric.shard import _canonical_json, _sha256

    items = ["a", "b", "c"]
    assert cycle_digest(items) != _sha256(_canonical_json(items))


def test_orbit_digest_is_hex_sha256():
    d = orbit_digest(["a", "b", "c"], [])
    assert len(d) == 64
    assert all(c in "0123456789abcdef" for c in d)


def test_empty_and_singleton_digests_are_defined():
    assert len(cycle_digest([])) == 64
    assert cycle_digest(["only"]) != cycle_digest([])


# ── serialization cost ───────────────────────────────────────────────


def test_elements_are_serialized_once_per_call(monkeypatch):
    """Ordering keys are computed once, then permuted — not recomputed.

    Guards against reintroducing the O(|G| x n) encode that the keyed
    internals removed.
    """
    import spiritwriter.fabric.orbit as orbit

    calls = []
    real = orbit._canonical_json
    monkeypatch.setattr(
        orbit, "_canonical_json", lambda x: (calls.append(x), real(x))[1]
    )

    canonical_cycle(list(range(50)))
    assert len(calls) == 50

    calls.clear()
    group = [[(i + s) % 8 for i in range(8)] for s in range(8)]
    canonical_under(list(range(8)), group)
    assert len(calls) == 8
