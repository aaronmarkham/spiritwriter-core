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

Element requirements
--------------------
Elements are ordered and hashed through the same canonical JSON the
shard layer uses, which has two consequences worth knowing:

* **Elements must be JSON-serializable.** ``str``, ``int``, ``float``,
  ``bool``, ``None``, and lists/dicts of those work directly. Anything
  else — ``bytes``, ``datetime``, ``set``, a domain object — needs a
  ``key=`` function mapping it onto a serializable surrogate, the same
  way :func:`sorted` takes a key. Without one you get a ``TypeError``
  naming the offending element.
* **JSON coerces dict keys to strings**, so ``{1: "a"}`` and
  ``{"1": "a"}`` canonicalize and digest identically. This is inherited
  from the shard layer's content addressing, not specific to this
  module, but it matters here because arbitrary dicts are valid
  elements. Pass ``key=`` if that collapse is wrong for your data.
* **Ordering is lexicographic over those JSON bytes**, so numbers sort
  as text: ``10`` precedes ``9``. Canonicalization only needs *a*
  deterministic total order, so this is correct either way — but if you
  want numeric order in the result, pass a text-sortable key:
  :func:`padded` for bounded non-negative integers, ISO-8601 for dates.

Digests are domain-separated, so an orbit digest never collides with an
unrelated content hash of the same bytes elsewhere in the fabric.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Callable, Iterable, Sequence

from spiritwriter.fabric.shard import _canonical_json, _sha256

__all__ = [
    "padded",
    "least_rotation",
    "canonical_cycle",
    "anchor_cycle",
    "cycle_digest",
    "apply_permutation",
    "permutation_closure",
    "canonical_under",
    "orbit_digest",
    "MAX_GROUP_ORDER",
]


KeyFn = Callable[[Any], Any]

# Digest domain separation. A digest from this module identifies "the
# canonical form of a structure under a declared symmetry" — not "these
# bytes". Prefixing keeps it from colliding with a plain content hash of
# the same sequence, and keeps cycle digests distinct from orbit
# digests. Version the tag rather than changing the scheme in place:
# persisted digests are only stable while the domain string is.
_CYCLE_DOMAIN = b"spiritwriter.fabric.orbit/cycle/v1\x00"
_ORBIT_DOMAIN = b"spiritwriter.fabric.orbit/orbit/v1\x00"

#: Safety cap on :func:`permutation_closure`. A generating set can
#: expand to a group far larger than the caller expects — two
#: permutations on 10 slots are enough to generate all 3.6 million —
#: so growth past this raises rather than exhausting memory.
MAX_GROUP_ORDER = 100_000


# ── Element ordering ─────────────────────────────────────────────────


def padded(width: int) -> KeyFn:
    """Build a ``key=`` that keeps non-negative integers in numeric order.

    Ordering here is lexicographic over canonical-JSON bytes, so ``10``
    sorts before ``9``. Zero-padding to a fixed width restores numeric
    order across a bounded range:

    >>> canonical_cycle([9, 10, 11], reflect=False, key=padded(3))
    (9, 10, 11)

    ``width`` is explicit and its bound is enforced: a value that does
    not fit raises rather than sorting silently into the wrong place —
    the exact failure this helper exists to prevent. Choose the width
    from the range's ceiling, not from the values you happen to hold
    today, and remember that a persisted digest is only stable while the
    width is.

    Dates need no helper: ISO-8601 already sorts as text, which is what
    :func:`spiritwriter.fabric.canonicalize.normalize_date` emits.
    """
    if width < 1:
        raise ValueError(f"width must be >= 1, got {width}")
    limit = 10 ** width

    def key(value: Any) -> str:
        # bool is an int subclass; padding True to "001" is never intended.
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(
                f"padded({width}) takes int, got {type(value).__name__}"
            )
        if value < 0:
            raise ValueError(
                f"padded({width}) takes non-negative ints, got {value}; "
                f"negative values do not zero-pad into sort order — offset "
                f"them into a non-negative range first"
            )
        if value >= limit:
            raise ValueError(
                f"{value} does not fit width={width} (max {limit - 1}); "
                f"it would sort out of order — widen the pad"
            )
        return f"{value:0{width}d}"

    return key


def _key_one(value: Any, key: KeyFn | None, where: str) -> bytes:
    """Canonical-JSON bytes for one element, with a locating error."""
    surrogate = key(value) if key is not None else value
    try:
        return _canonical_json(surrogate)
    except TypeError as exc:
        raise TypeError(
            f"{where} is not JSON-serializable "
            f"({type(surrogate).__name__}); pass key= to map elements onto "
            f"a serializable surrogate, e.g. key=lambda x: x.isoformat()"
        ) from exc


def _keys(seq: Sequence[Any], key: KeyFn | None = None) -> list[bytes]:
    """Map elements to sortable bytes via canonical JSON.

    Gives a total order over mixed-type sequences without relying on
    ``<`` between unrelated types, and is stable across processes
    because it is the same serialization the shard layer hashes with.
    """
    return [_key_one(x, key, f"element at index {i}") for i, x in enumerate(seq)]


def _join(keys: Sequence[bytes]) -> bytes:
    """Concatenate element keys into the canonical JSON of the list.

    Byte-identical to ``_canonical_json(list(elements))`` because
    ``_canonical_json`` uses ``(",", ":")`` separators — so the digest
    stays reproducible by anyone hashing the canonical form directly,
    without paying a second serialization pass here.
    """
    return b"[" + b",".join(keys) + b"]"


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


def _canonical_cycle_keyed(
    items: tuple, keys: Sequence[bytes], reflect: bool
) -> tuple[tuple, tuple]:
    """Canonical form as ``(elements, element_keys)``.

    Both halves come back so callers that want the digest do not pay a
    second serialization pass over the same elements.
    """
    forward_at = least_rotation(keys)
    forward_keys = _rotate(keys, forward_at)
    if not reflect:
        return _rotate(items, forward_at), forward_keys

    mirror_items = items[::-1]
    mirror_keys = list(keys)[::-1]
    backward_at = least_rotation(mirror_keys)
    backward_keys = _rotate(mirror_keys, backward_at)

    if forward_keys <= backward_keys:
        return _rotate(items, forward_at), forward_keys
    return _rotate(mirror_items, backward_at), backward_keys


def canonical_cycle(
    seq: Sequence[Any], *, reflect: bool = True, key: KeyFn | None = None
) -> tuple[Any, ...]:
    """Canonical representative of a cyclic sequence.

    ``reflect=True`` (default) treats a cycle and its reversal as the
    same structure — the right choice for undirected rings, where
    traversal direction is an artifact of how the record was written.
    Set ``reflect=False`` when direction carries meaning (an ordered
    pipeline, a signed edge) and the mirror image is a *different*
    structure.

    ``key`` maps each element onto a JSON-serializable surrogate used
    for ordering, exactly like the ``key`` argument to :func:`sorted`.
    The returned tuple always holds the *original* elements.

    Ordering is lexicographic over canonical-JSON bytes, so numbers sort
    as text (``10`` before ``9``) and a ``key`` that returns numbers
    inherits that. Use :func:`padded` for bounded non-negative integers,
    or ISO-8601 for dates, if you need the result to read in value order.

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

    items = tuple(seq)
    form, _ = _canonical_cycle_keyed(items, _keys(items, key), reflect)
    return form


def anchor_cycle(
    seq: Sequence[Any], anchor: Any, *, key: KeyFn | None = None
) -> tuple[Any, ...]:
    """Rotate a cycle so that ``anchor`` sits in position 0.

    Use when the structure has a distinguished element — a subject
    entity, a root, a shard the ring belongs to — and you want every
    record about that structure written from the same starting point.
    Cheaper than a full canonical form and preserves the caller's
    traversal direction.

    ``key`` is applied to the sequence elements *and* to ``anchor``, so
    the anchor is matched on its surrogate rather than on Python
    equality.

    Matching is on exact canonical-JSON bytes, so a ``key`` that returns
    numbers matches on their text form — ``1`` and ``1.0`` are distinct
    anchors.

    Raises ``ValueError`` if the anchor is absent, or if it appears more
    than once and the rotation would therefore be ambiguous.
    """
    anchor_key = _key_one(anchor, key, "anchor")
    keys = _keys(seq, key)
    hits = [i for i, k in enumerate(keys) if k == anchor_key]

    if not hits:
        raise ValueError("anchor not present in sequence")
    if len(hits) > 1:
        raise ValueError(
            f"anchor appears {len(hits)} times; rotation is ambiguous"
        )

    return _rotate(seq, hits[0])


def cycle_digest(
    seq: Sequence[Any], *, reflect: bool = True, key: KeyFn | None = None
) -> str:
    """Content-addressed digest of a cycle's equivalence class.

    Every rotation (and, by default, reversal) of the same ring hashes
    to the same value, so the digest is usable directly as a dedup key
    or shard id component.

    The digest covers the canonical form's element *surrogates* — the
    values ``key`` produces — so two rings differing only in detail the
    key discards share a digest, by construction.

    Ordering is lexicographic over canonical-JSON bytes, so numbers sort
    as text (``10`` before ``9``) and a ``key`` that returns numbers
    inherits that. Use :func:`padded` for bounded non-negative integers,
    or ISO-8601 for dates, if you need the result to read in value order.
    """
    if len(seq) <= 1:
        return _sha256(_CYCLE_DOMAIN + _join(_keys(seq, key)))

    items = tuple(seq)
    _, form_keys = _canonical_cycle_keyed(items, _keys(items, key), reflect)
    return _sha256(_CYCLE_DOMAIN + _join(form_keys))


# ── General permutation symmetry ─────────────────────────────────────


def _validate_perm(perm: Sequence[int], n: int) -> tuple[int, ...]:
    if len(perm) != n:
        raise ValueError(f"permutation has length {len(perm)}, items has {n}")
    if sorted(perm) != list(range(n)):
        raise ValueError("perm is not a permutation of range(len(items))")
    return tuple(perm)


def apply_permutation(
    items: Sequence[Any], perm: Sequence[int]
) -> tuple[Any, ...]:
    """Relabel ``items`` by ``perm``: result[i] is items[perm[i]].

    ``perm`` must be a permutation of ``range(len(items))``.
    """
    _validate_perm(perm, len(items))
    return tuple(items[perm[i]] for i in range(len(items)))


def _compose(p: Sequence[int], q: Sequence[int]) -> tuple[int, ...]:
    """Permutation whose relabeling equals ``p`` applied, then ``q``."""
    return tuple(p[q[i]] for i in range(len(p)))


def permutation_closure(
    perms: Iterable[Sequence[int]],
    n: int,
    *,
    max_order: int = MAX_GROUP_ORDER,
) -> tuple[tuple[int, ...], ...]:
    """The group generated by ``perms``, including the identity.

    Callers think in terms of the symmetries they can name — "rotation
    by one", "reflection" — not the closed group those generate. Feeding
    a *generating set* straight into a canonicalizer is a silent
    correctness bug: two members of one orbit reduce to different
    representatives, because neither is reachable from the other in a
    single step. Closing the set first is what makes declaring the
    symmetry sufficient.

    Every element of a finite permutation set has finite order, so
    closing under composition also supplies the inverses; no separate
    inversion pass is needed.

    Cost is O(|G| x |perms|) compositions, so prefer a small generating
    set over an already-closed group where you have the choice. Raises
    ``ValueError`` if the group exceeds ``max_order``.

    >>> permutation_closure([[1, 2, 0]], 3)
    ((0, 1, 2), (1, 2, 0), (2, 0, 1))
    """
    identity = tuple(range(n))
    generators: list[tuple[int, ...]] = []
    for perm in perms:
        validated = _validate_perm(perm, n)
        if validated != identity and validated not in generators:
            generators.append(validated)

    if not generators:
        return (identity,)

    group = {identity}
    frontier = deque([identity])
    while frontier:
        current = frontier.popleft()
        for gen in generators:
            candidate = _compose(current, gen)
            if candidate not in group:
                if len(group) >= max_order:
                    raise ValueError(
                        f"generated group exceeds max_order={max_order}; "
                        f"pass a smaller generating set or raise the cap"
                    )
                group.add(candidate)
                frontier.append(candidate)

    return tuple(sorted(group))


def _canonical_under_keyed(
    items: tuple,
    keys: Sequence[bytes],
    perms: Iterable[Sequence[int]],
    close: bool,
    max_order: int,
) -> tuple[tuple, tuple]:
    n = len(items)
    if close:
        group: tuple[tuple[int, ...], ...] = permutation_closure(
            perms, n, max_order=max_order
        )
    else:
        group = (tuple(range(n)),) + tuple(_validate_perm(p, n) for p in perms)

    best_items = items
    best_keys = tuple(keys)
    for perm in group:
        candidate_keys = tuple(keys[perm[i]] for i in range(n))
        if candidate_keys < best_keys:
            best_keys = candidate_keys
            best_items = tuple(items[perm[i]] for i in range(n))

    return best_items, best_keys


def canonical_under(
    items: Sequence[Any],
    perms: Iterable[Sequence[int]],
    *,
    key: KeyFn | None = None,
    close: bool = True,
    max_order: int = MAX_GROUP_ORDER,
) -> tuple[Any, ...]:
    """Least representative of ``items`` under a group of permutations.

    Generalizes :func:`canonical_cycle` to any declared symmetry: pass
    the permutations that you consider to leave the structure unchanged
    and get back the one member of the resulting orbit that every
    equivalent input also maps to.

    ``perms`` is treated as a *generating set* and closed under
    composition first (see :func:`permutation_closure`), which is what
    makes that guarantee hold. Passing an already-closed group is fine
    and costs one redundant closure pass; set ``close=False`` to skip it
    when the group is large and you have closed it yourself.

    .. warning::
       ``close=False`` with a set that is *not* closed under composition
       silently returns different representatives for different members
       of the same orbit — the exact failure this function exists to
       prevent. Disable closure only when you are certain.

    The identity is always included, so empty ``perms`` yields the input
    itself: a well-defined result under the trivial symmetry.

    Ordering is lexicographic over canonical-JSON bytes, so numbers sort
    as text (``10`` before ``9``) and a ``key`` that returns numbers
    inherits that. Use :func:`padded` for bounded non-negative integers,
    or ISO-8601 for dates, if you need the result to read in value order.
    """
    tuple_items = tuple(items)
    keys = _keys(tuple_items, key)
    form, _ = _canonical_under_keyed(tuple_items, keys, perms, close, max_order)
    return form


def orbit_digest(
    items: Sequence[Any],
    perms: Iterable[Sequence[int]],
    *,
    key: KeyFn | None = None,
    close: bool = True,
    max_order: int = MAX_GROUP_ORDER,
) -> str:
    """Content-addressed digest of an orbit under a permutation group.

    Same closure semantics as :func:`canonical_under`: ``perms`` is a
    generating set, closed before the canonical form is chosen.

    Ordering is lexicographic over canonical-JSON bytes, so numbers sort
    as text (``10`` before ``9``) and a ``key`` that returns numbers
    inherits that. Use :func:`padded` for bounded non-negative integers,
    or ISO-8601 for dates, if you need the result to read in value order.
    """
    tuple_items = tuple(items)
    keys = _keys(tuple_items, key)
    _, form_keys = _canonical_under_keyed(
        tuple_items, keys, perms, close, max_order
    )
    return _sha256(_ORBIT_DOMAIN + _join(form_keys))
