"""inmate_clean — realistic frio production drift only.

Subset of the people-corpus drift families covering the
transcription-error and roster-format-variation modes that actually
appear in real jail roster data. Deliberately EXCLUDES the stress-test
modes (`surname_duplication`, `surname_hyphenate_duplicate`,
`four_name_compress`, `diminutive`) — those exist in `people` to test
engine resilience under unrealistic adversarial drift.

Compare per-family results between `inmate_clean` and `people` to see
the realistic-operating-regime numbers vs the upper-bound stress test.
"""

from __future__ import annotations

from typing import Any

from benchmarks.eval.ess_accuracy.mutations import Mutation, MutationFamily


def _split_surnames(last_name: str) -> list[str]:
    parts: list[str] = []
    for chunk in last_name.split():
        parts.extend(p for p in chunk.split("-") if p)
    return parts


def _copy_with(record: dict[str, Any], **fields: Any) -> dict[str, Any]:
    out = dict(record)
    out.update(fields)
    return out


def _gen_middle_initial_add(record, ess_fields):
    first = record.get("first_name", "")
    if not isinstance(first, str) or not first or " " in first:
        return []
    new_first = f"{first} A"
    return [Mutation(
        family="middle_initial_add",
        mutated=_copy_with(record, first_name=new_first),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{first!r} -> {new_first!r}",
    )]


def _gen_middle_initial_drop(record, ess_fields):
    first = record.get("first_name", "")
    if not isinstance(first, str):
        return []
    parts = first.split()
    if len(parts) < 2 or len(parts[-1]) != 1:
        return []
    new_first = " ".join(parts[:-1])
    return [Mutation(
        family="middle_initial_drop",
        mutated=_copy_with(record, first_name=new_first),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{first!r} -> {new_first!r}",
    )]


def _gen_surname_drop_maternal(record, ess_fields):
    """Garcia Lopez -> Garcia (drop maternal/second surname).

    Real frio drift: some rosters keep both surnames, some keep only the
    paternal one. Cross-jurisdictional records often disagree.
    """
    last = record.get("last_name", "")
    if not isinstance(last, str) or not last:
        return []
    parts = _split_surnames(last)
    if len(parts) < 2:
        return []
    return [Mutation(
        family="surname_drop_maternal",
        mutated=_copy_with(record, last_name=parts[0]),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{last!r} -> {parts[0]!r}",
    )]


def _gen_surname_hyphenate(record, ess_fields):
    """Garcia Lopez -> Garcia-Lopez (space-to-hyphen normalization drift)."""
    last = record.get("last_name", "")
    if not isinstance(last, str) or " " not in last:
        return []
    new_last = last.replace(" ", "-")
    return [Mutation(
        family="surname_hyphenate",
        mutated=_copy_with(record, last_name=new_last),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{last!r} -> {new_last!r}",
    )]


def _gen_surname_dehyphenate(record, ess_fields):
    """Smith-Jones -> Smith Jones (hyphen-to-space drift)."""
    last = record.get("last_name", "")
    if not isinstance(last, str) or "-" not in last:
        return []
    new_last = last.replace("-", " ")
    return [Mutation(
        family="surname_dehyphenate",
        mutated=_copy_with(record, last_name=new_last),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{last!r} -> {new_last!r}",
    )]


FAMILIES: list[MutationFamily] = [
    MutationFamily("middle_initial_add",    _gen_middle_initial_add,
                   "Insert trailing single-letter middle initial."),
    MutationFamily("middle_initial_drop",   _gen_middle_initial_drop,
                   "Drop trailing single-letter middle initial."),
    MutationFamily("surname_drop_maternal", _gen_surname_drop_maternal,
                   "Garcia Lopez -> Garcia (drop second surname)."),
    MutationFamily("surname_hyphenate",     _gen_surname_hyphenate,
                   "Garcia Lopez -> Garcia-Lopez."),
    MutationFamily("surname_dehyphenate",   _gen_surname_dehyphenate,
                   "Smith-Jones -> Smith Jones."),
]
