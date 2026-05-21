"""Mutation rule families for ESS accuracy testing.

A ``Mutation`` is a deterministic transformation of a canonical entity
record producing a variant form with a stated expected resolution tier.

A ``MutationFamily`` groups mutations of the same type so per-family
recall can be reported (e.g. "case variation: 100% recall at T1").

Universal families apply to every corpus. Domain-specific families live
in each corpus's ``mutations.py``.
"""

from __future__ import annotations

import hashlib
import random
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Mutation:
    """A single derived record + ground-truth match expectation."""
    family: str                       # e.g. "case", "typo_insertion"
    mutated: dict[str, Any]           # the variant record
    canonical: dict[str, Any]         # the original record this mutates
    same_entity: bool                 # ground truth — should resolve to canonical?
    expected_tier_min: str | None     # "t1_exact", "t2_strong", "t3_fuzzy", or None for negatives
    notes: str = ""                   # human-readable description


@dataclass
class MutationFamily:
    """A named generator producing mutations of one type."""
    name: str
    generate: Callable[[dict[str, Any], list[str]], list[Mutation]]
    description: str = ""


# ── Helpers ─────────────────────────────────────────────────────────


def _copy_with(record: dict[str, Any], **fields: Any) -> dict[str, Any]:
    out = dict(record)
    out.update(fields)
    return out


def _string_fields(record: dict[str, Any], ess_fields: list[str]) -> list[str]:
    """Return ESS field names whose values are strings (skip dates, ints, etc.)."""
    return [
        f for f in ess_fields
        if isinstance(record.get(f), str) and record[f]
    ]


# ── Universal mutation generators ───────────────────────────────────


def _gen_case(record: dict[str, Any], ess_fields: list[str]) -> list[Mutation]:
    """Uppercase / lowercase / Title Case variations of every string ESS field."""
    out: list[Mutation] = []
    fields = _string_fields(record, ess_fields)
    for f in fields:
        original = record[f]
        for transform_name, transform in (
            ("upper", str.upper),
            ("lower", str.lower),
            ("title", str.title),
        ):
            new_val = transform(original)
            if new_val == original:
                continue
            out.append(Mutation(
                family="case",
                mutated=_copy_with(record, **{f: new_val}),
                canonical=record,
                same_entity=True,
                expected_tier_min="t1_exact",
                notes=f"{f}: {transform_name}",
            ))
    return out


def _gen_whitespace(record: dict[str, Any], ess_fields: list[str]) -> list[Mutation]:
    """Pad ESS fields with leading/trailing/internal whitespace."""
    out: list[Mutation] = []
    fields = _string_fields(record, ess_fields)
    for f in fields:
        original = record[f]
        for label, new_val in (
            ("leading", f"  {original}"),
            ("trailing", f"{original}  "),
            ("both", f"  {original}  "),
        ):
            out.append(Mutation(
                family="whitespace",
                mutated=_copy_with(record, **{f: new_val}),
                canonical=record,
                same_entity=True,
                expected_tier_min="t1_exact",
                notes=f"{f}: {label}",
            ))
    return out


def _gen_typo_substitution(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Substitute one character in each string ESS field (length-preserving)."""
    out: list[Mutation] = []
    rng = random.Random(_seed(record, "typo_sub"))
    fields = _string_fields(record, ess_fields)
    for f in fields:
        original = record[f]
        if len(original) < 4:
            continue
        idx = rng.randrange(1, len(original) - 1)  # avoid first/last chars
        sub_char = _shift_char(original[idx], rng)
        if sub_char == original[idx]:
            continue
        new_val = original[:idx] + sub_char + original[idx + 1:]
        out.append(Mutation(
            family="typo_substitution",
            mutated=_copy_with(record, **{f: new_val}),
            canonical=record,
            same_entity=True,
            expected_tier_min="t2_strong",
            notes=f"{f}: {original!r} -> {new_val!r}",
        ))
    return out


def _gen_typo_insertion(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Insert one duplicate character in each string ESS field."""
    out: list[Mutation] = []
    rng = random.Random(_seed(record, "typo_ins"))
    fields = _string_fields(record, ess_fields)
    for f in fields:
        original = record[f]
        if len(original) < 4:
            continue
        idx = rng.randrange(1, len(original) - 1)
        new_val = original[:idx] + original[idx] + original[idx:]
        out.append(Mutation(
            family="typo_insertion",
            mutated=_copy_with(record, **{f: new_val}),
            canonical=record,
            same_entity=True,
            expected_tier_min="t2_strong",
            notes=f"{f}: {original!r} -> {new_val!r}",
        ))
    return out


def _gen_unicode_normalization(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Strip diacritics: "María" -> "Maria"."""
    out: list[Mutation] = []
    fields = _string_fields(record, ess_fields)
    for f in fields:
        original = record[f]
        stripped = "".join(
            c for c in unicodedata.normalize("NFD", original)
            if unicodedata.category(c) != "Mn"
        )
        if stripped == original:
            continue
        out.append(Mutation(
            family="unicode_normalization",
            mutated=_copy_with(record, **{f: stripped}),
            canonical=record,
            same_entity=True,
            expected_tier_min="t2_strong",
            notes=f"{f}: {original!r} -> {stripped!r}",
        ))
    return out


def _gen_negative_control(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Replace each ESS field with a clearly-different value (one at a time).

    This is the partial-overlap false-merge canary — these MUST NOT
    resolve to T1/T2.

    Note on scope: mutations garble one ESS field at a time, leaving
    the others intact. With a 3-field schema this produces records that
    share 2 of 3 anchors with the canonical — a *harder* false-merge
    test than fully-disjoint records, because the engine has to refuse
    to merge despite partial agreement. Worth keeping in mind when
    comparing the negative_control row to a Jaccard-on-disjoint-records
    baseline. Complemented by ``garbled_all_fields`` (below) which
    tests the no-overlap path.
    """
    out: list[Mutation] = []
    fields = _string_fields(record, ess_fields)
    for f in fields:
        original = record[f]
        # Use a value the registry would never legitimately confuse.
        new_val = "ZZZ" + original[::-1] + "QQQ"
        out.append(Mutation(
            family="negative_control",
            mutated=_copy_with(record, **{f: new_val}),
            canonical=record,
            same_entity=False,
            expected_tier_min=None,
            notes=f"{f}: garbled to {new_val!r}",
        ))
    return out


def _gen_garbled_all_fields(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Replace EVERY string ESS field with a garbled value at once.

    The no-overlap false-merge canary. Tests the cleanest rejection
    path: when nothing matches, the engine should land at NO_MATCH (or
    very weakly at T4 via context fields like gender), never T1/T2/T3.

    Complements ``negative_control`` which tests the partial-overlap
    case (one field garbled, others intact — engine has to refuse to
    auto-merge despite 2/3 fields matching). This family tests the
    full-disjoint case: nothing matches, engine should cleanly reject.

    Generates one mutation per record.
    """
    fields = _string_fields(record, ess_fields)
    if not fields:
        return []
    new = dict(record)
    for f in fields:
        original = record[f]
        new[f] = "ZZZ" + original[::-1] + "QQQ"
    return [Mutation(
        family="garbled_all_fields",
        mutated=new,
        canonical=record,
        same_entity=False,
        expected_tier_min=None,
        notes="all ess_fields garbled (no-overlap negative test)",
    )]


# ── Family registry ─────────────────────────────────────────────────


UNIVERSAL_FAMILIES: list[MutationFamily] = [
    MutationFamily("case", _gen_case,
                   "Upper/lower/title-case variations of ESS fields."),
    MutationFamily("whitespace", _gen_whitespace,
                   "Leading/trailing/internal whitespace padding."),
    MutationFamily("typo_substitution", _gen_typo_substitution,
                   "Single-char substitution in ESS fields."),
    MutationFamily("typo_insertion", _gen_typo_insertion,
                   "Single-char insertion (duplicate) in ESS fields."),
    MutationFamily("unicode_normalization", _gen_unicode_normalization,
                   "Strip combining diacritics (María -> Maria)."),
    MutationFamily("negative_control", _gen_negative_control,
                   "Partial-overlap negative: 1 of N ESS fields garbled."),
    MutationFamily("garbled_all_fields", _gen_garbled_all_fields,
                   "No-overlap negative: ALL ESS fields garbled. MUST land at NO_MATCH or T4 (via context-only signal)."),
]


# ── Internal utilities ──────────────────────────────────────────────


def _seed(record: dict[str, Any], salt: str) -> int:
    """Deterministic per-record seed so the same canonical produces the same
    mutation — *across processes*, not just within one.

    Python's built-in ``hash()`` is randomized per-process via
    ``PYTHONHASHSEED``, which means typo positions chosen by a Random()
    instance seeded with hash() would differ between runs. Use
    blake2b so trend-tracking and reproducibility actually hold.
    """
    key = salt + "|" + "|".join(
        f"{k}={v}" for k, v in sorted(record.items()) if v is not None
    )
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big")


_ALPHA = "abcdefghijklmnopqrstuvwxyz"


def _shift_char(c: str, rng: random.Random) -> str:
    """Return a letter different from c (preserves case for ASCII letters)."""
    if c.isupper() and c.isalpha():
        choices = [x.upper() for x in _ALPHA if x.upper() != c]
    elif c.islower() and c.isalpha():
        choices = [x for x in _ALPHA if x != c]
    else:
        return c  # non-letter: skip
    return rng.choice(choices)
