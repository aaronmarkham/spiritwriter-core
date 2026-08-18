"""Structures, symmetry operations, and pair generation.

The ESS harness next door mutates *fields* — case, whitespace, typos,
unicode. Every one of those makes a record's text a little different
while leaving its shape alone, which is why similarity scoring handles
them and why the rule's wins there are mostly ``.lower()``.

This harness mutates *shape*. A ring recorded from a different starting
point has the same members in the same cyclic order and a completely
different string. That is the case similarity scoring cannot reach, and
measuring it needs structures that have a declared symmetry in the first
place.

Three kinds, each with a different symmetry and therefore a different
notion of "the same":

``ring``
    Undirected loop of see-also references. Rotation and reflection both
    preserve it — 2n ways to write one down.
``directed_ring``
    Ordered cycle where traversal direction carries meaning (a pipeline,
    a signed edge). Rotation preserves it; reflection does **not**, and a
    harness that merged the two would be over-merging.
``grouping``
    Co-equal terms with no principal member. Every permutation is the
    same grouping — n! ways to write one down.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass

from spiritwriter.fabric import canonical_under, cycle_digest, orbit_digest

# Sampling caps. A grouping of 6 has 720 recordings and 5 structures
# would put 3,600 rows in the report for no extra signal; sample instead
# and say so in the output rather than silently truncating.
MAX_RECORDINGS = 24


@dataclass(frozen=True)
class Structure:
    """One structure plus the symmetry it is allowed to have."""

    id: str
    kind: str
    members: tuple[str, ...]

    def digest(self) -> str:
        """The rule's answer: one value for the whole equivalence class."""
        if self.kind == "ring":
            return cycle_digest(self.members, reflect=True)
        if self.kind == "directed_ring":
            return cycle_digest(self.members, reflect=False)
        if self.kind == "grouping":
            return orbit_digest(self.members, _adjacent_swaps(len(self.members)))
        raise ValueError(f"unknown kind {self.kind!r}")

    def canonical(self) -> tuple:
        if self.kind == "grouping":
            return canonical_under(self.members, _adjacent_swaps(len(self.members)))
        from spiritwriter.fabric import canonical_cycle

        return canonical_cycle(self.members, reflect=self.kind == "ring")


def _adjacent_swaps(n: int) -> list[list[int]]:
    """Generators of the symmetric group on n slots.

    Deliberately a *generating set*, not the closed group — exercising
    the path where the caller declares the symmetry it can name and the
    closure is the library's problem.
    """
    return [
        [j if j not in (i, i + 1) else (i + 1 if j == i else i) for j in range(n)]
        for i in range(n - 1)
    ]


def recordings(s: Structure, rng: random.Random) -> list[tuple[str, ...]]:
    """Every way this structure could legitimately be written down."""
    m, n = list(s.members), len(s.members)
    rots = [tuple(m[k:] + m[:k]) for k in range(n)]

    if s.kind == "directed_ring":
        out = rots
    elif s.kind == "ring":
        rev = m[::-1]
        out = rots + [tuple(rev[k:] + rev[:k]) for k in range(n)]
    else:
        out = [tuple(p) for p in itertools.permutations(m)]

    out = sorted(set(out))
    if len(out) > MAX_RECORDINGS:
        # Always keep the as-authored form; sample the rest.
        rest = [r for r in out if r != tuple(m)]
        out = [tuple(m)] + rng.sample(rest, MAX_RECORDINGS - 1)
    return out


def distinct_siblings(s: Structure, rng: random.Random) -> list[Structure]:
    """Structures over the *same members* that are genuinely different.

    These are the hard negatives, and the whole reason this harness
    exists. They share every token with the original, so a text
    similarity score rates them highly — while the rule, correctly,
    keeps them apart. A negative set of unrelated structures would make
    the baseline look far better than it is.
    """
    if s.kind == "grouping":
        return []  # every permutation IS the same grouping; no siblings

    seen, out = {s.digest()}, []
    for p in itertools.permutations(s.members):
        cand = Structure(f"{s.id}~{len(out)}", s.kind, tuple(p))
        d = cand.digest()
        if d not in seen:
            seen.add(d)
            out.append(cand)
    rng.shuffle(out)
    return out[:MAX_RECORDINGS]


# ── Corpora ──────────────────────────────────────────────────────────

CORPORA: dict[str, list[Structure]] = {
    "subject_rings": [
        Structure("cartography", "ring", (
            "Cartography", "Map projections", "Geodesy",
            "Surveying", "Topographic mapping")),
        Structure("printing", "ring", (
            "Printing", "Typography", "Bookbinding", "Papermaking")),
        Structure("hydrology", "ring", (
            "Hydrology", "Watersheds", "Groundwater",
            "Aquifers", "Water quality")),
        Structure("navigation", "ring", (
            "Navigation", "Celestial navigation", "Dead reckoning",
            "Nautical charts", "Compass")),
    ],
    "pipelines": [
        Structure("ingest", "directed_ring", (
            "fetch", "parse", "normalize", "store", "index")),
        Structure("review", "directed_ring", (
            "draft", "review", "revise", "approve")),
        Structure("release", "directed_ring", (
            "build", "test", "stage", "sign", "publish")),
    ],
    "coauthors": [
        Structure("paper-a", "grouping", (
            "Vaswani", "Shazeer", "Parmar", "Uszkoreit")),
        Structure("paper-b", "grouping", (
            "Devlin", "Chang", "Lee", "Toutanova")),
        Structure("paper-c", "grouping", (
            "Brown", "Mann", "Ryder", "Subbiah", "Kaplan")),
    ],
}
