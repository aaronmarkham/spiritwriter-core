"""Canonical forms for structures that carry a symmetry.

Two records can describe the same structure while differing only by an
allowed symmetry: a ring of relations read from a different starting
point, an edge list traversed in the opposite direction, a tuple whose
slots are interchangeable. Comparing those by similarity is a threshold
decision. Comparing their *canonical forms* is a byte comparison.

This module supplies the exact, deterministic half of that problem:
given a structure and the symmetry it is allowed to have, produce the
unique lexicographically-minimal representative of its equivalence
class, plus a content-addressed digest of that representative.

No embeddings, no thresholds, no scoring. Either two structures land on
the same canonical form or they do not.

Design notes
------------
* Element ordering uses :func:`_canonical_json`, so heterogeneous
  sequences have a stable total order that does not depend on Python's
  cross-type comparison rules and does not change between runs.
* :func:`least_rotation` is Booth's algorithm, O(n), not the naive
  O(n^2) scan over rotations.
* The caller declares the symmetry. ``reflect=False`` keeps chirality
  meaningful; ``reflect=True`` treats a ring and its mirror as one.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from spiritwriter.fabric.shard import _canonical_json, _sha256

__all__ = [
    "least_rotation",
    "canonical_cycle",
    "anchor_cycle",
    "cycle_digest",
    "apply_permutation",
    "canonical_under",
    "orbit_digest",
]


# ── Element ordering ─────────────────────────────────────────────────


def _keys(seq: Sequence[Any]) -> list[bytes]:
    """Map elements to sortable bytes via canonical JSON.

    Gives a total order over mixed-type sequences without relying on
    ``<`` between unrelated types, and is stable across processes
    because it is the same serialization the shard layer hashes with.
    """
    return [_canonical_json(x) for x in seq]


# ── Cyclic structures ────────────────────────────────────────────────


def least_rotation(keys: Sequence[bytes]) -> int:
    """Index at which the lexicographically least rotation begins.

    Booth's algorithm. Runs in O(n) and is correct in the presence of
    repeated elements, where a naive "rotate until it looks smallest"
    scan is both slower and easy to get wrong.
    """
    n = len(keys)
    if n == 0:
        return 0

    s = list(keys) * 2
    failure = [-1] * (2 * n)
    k = 0

    for j in range(1, 2 * n):
        sj = s[j]
        i = failure[j - k - 1]
        while i != -1 and sj != s[k + i + 1]:
            if sj < s[k + i + 1]:
                k = j - i - 1
            i = failure[i]
        if sj != s[k + i + 1]:
            if sj < s[k]:
                k = j
            failure[j - k] = -1
        else:
            failure[j - k] = i + 1

    return k


def _rotate(seq: Sequence[Any], start: int) -> tuple:
    n = len(seq)
    if n == 0:
        return ()
    return tuple(seq[(start + i) % n] for i in range(n))


def canonical_cycle(seq: Sequence[Any], *, reflect: bool = True) -> tuple:
    """Canonical representative of a cyclic sequence.

    ``reflect=True`` (default) treats a cycle and its reversal as the
    same structure — the right choice for undirected rings, where
    traversal direction is an artifact of how the record was written.
    Set ``reflect=False`` when direction carries meaning (an ordered
    pipeline, a signed edge) and the mirror image is a *different*
    structure.

    Returns a tuple of the original elements. It is usable as a dict key
    when those elements are themselves hashable; when they are not (dicts,
    lists), use :func:`cycle_digest` as the handle instead.

    >>> canonical_cycle(["c", "a", "b"]) == canonical_cycle(["a", "b", "c"])
    True
    >>> canonical_cycle(["a", "c", "b"], reflect=False) == canonical_cycle(
    ...     ["a", "b", "c"], reflect=False
    ... )
    False
    """
    if len(seq) <= 1:
        return tuple(seq)

    forward = _rotate(seq, least_rotation(_keys(seq)))
    if not reflect:
        return forward

    reversed_seq = list(seq)[::-1]
    backward = _rotate(reversed_seq, least_rotation(_keys(reversed_seq)))

    return forward if _keys(forward) <= _keys(backward) else backward


def anchor_cycle(seq: Sequence[Any], anchor: Any) -> tuple:
    """Rotate a cycle so that ``anchor`` sits in position 0.

    Use when the structure has a distinguished element — a subject
    entity, a root, a shard the ring belongs to — and you want every
    record about that structure written from the same starting point.
    Cheaper than a full canonical form and preserves the caller's
    traversal direction.

    Raises ``ValueError`` if the anchor is absent, or if it appears more
    than once and the rotation would therefore be ambiguous.
    """
    key = _canonical_json(anchor)
    keys = _keys(seq)
    hits = [i for i, k in enumerate(keys) if k == key]

    if not hits:
        raise ValueError("anchor not present in sequence")
    if len(hits) > 1:
        raise ValueError(
            f"anchor appears {len(hits)} times; rotation is ambiguous"
        )

    return _rotate(seq, hits[0])


def cycle_digest(seq: Sequence[Any], *, reflect: bool = True) -> str:
    """Content-addressed digest of a cycle's equivalence class.

    Every rotation (and, by default, reversal) of the same ring hashes
    to the same value, so the digest is usable directly as a dedup key
    or shard id component.
    """
    return _sha256(_canonical_json(list(canonical_cycle(seq, reflect=reflect))))


# ── General permutation symmetry ─────────────────────────────────────


def apply_permutation(items: Sequence[Any], perm: Sequence[int]) -> tuple:
    """Relabel ``items`` by ``perm``: result[i] is items[perm[i]].

    ``perm`` must be a permutation of ``range(len(items))``.
    """
    if len(perm) != len(items):
        raise ValueError(
            f"permutation has length {len(perm)}, items has {len(items)}"
        )
    if sorted(perm) != list(range(len(items))):
        raise ValueError("perm is not a permutation of range(len(items))")

    return tuple(items[perm[i]] for i in range(len(items)))


def canonical_under(
    items: Sequence[Any], perms: Iterable[Sequence[int]]
) -> tuple:
    """Least representative of ``items`` under a group of permutations.

    Generalizes :func:`canonical_cycle` to any declared symmetry: pass
    the permutations that you consider to leave the structure unchanged
    and get back the one member of the resulting orbit that every
    equivalent input also maps to.

    The identity is always included, so an empty or partial ``perms``
    still yields a well-defined result — never one that is *smaller*
    than the input's own orbit warrants.
    """
    best = tuple(items)
    best_key = _keys(best)

    for perm in perms:
        candidate = apply_permutation(items, perm)
        candidate_key = _keys(candidate)
        if candidate_key < best_key:
            best, best_key = candidate, candidate_key

    return best


def orbit_digest(items: Sequence[Any], perms: Iterable[Sequence[int]]) -> str:
    """Content-addressed digest of an orbit under a permutation group."""
    return _sha256(_canonical_json(list(canonical_under(items, perms))))
