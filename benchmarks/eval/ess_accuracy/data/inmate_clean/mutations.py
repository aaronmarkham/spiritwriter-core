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

import datetime
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


# ── Falsification battery ─────────────────────────────────────────


def _gen_dob_typo(record: dict[str, Any], ess_fields: list[str]) -> list[Mutation]:
    """Shift DOB by 1 day — off-by-one data-entry typo."""
    dob = record.get("dob", "")
    if not isinstance(dob, str) or len(dob) != 10:
        return []
    try:
        d = datetime.date.fromisoformat(dob)
        new_d = (d + datetime.timedelta(days=1)).isoformat()
    except (ValueError, OverflowError):
        return []
    return [Mutation(
        family="dob_typo",
        mutated=_copy_with(record, dob=new_d),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{dob!r} -> {new_d!r} (off-by-one-day typo)",
    )]


_COLLISIONS: dict[str, dict[str, Any]] = {
    "Smith|James|1982-04-14": {
        "last_name": "Smith", "first_name": "James",
        "dob": "1982-09-30", "gender": "M",
    },
    "Johnson|Mary|1976-09-23": {
        "last_name": "Johnson", "first_name": "Mary",
        "dob": "1976-12-15", "gender": "F",
    },
    "Tanaka|Hiroshi|1985-10-20": {
        "last_name": "Tanaka", "first_name": "Hiroshi",
        "dob": "1985-12-25", "gender": "M",
    },
}


def _gen_realistic_collision(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """'Different inmate, same name, close DOB' — MUST NOT auto-merge."""
    key = (
        f"{record.get('last_name','')}|{record.get('first_name','')}"
        f"|{record.get('dob','')}"
    )
    pair = _COLLISIONS.get(key)
    if pair is None:
        return []
    return [Mutation(
        family="realistic_collision",
        mutated=pair,
        canonical=record,
        same_entity=False,
        expected_tier_min=None,
        notes=(
            f"different inmate sharing 2/3 ess_fields: "
            f"dob {record.get('dob')!r} vs {pair['dob']!r}"
        ),
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
    MutationFamily("dob_typo",              _gen_dob_typo,
                   "Off-by-one-day DOB typo (falsification battery)."),
    MutationFamily("realistic_collision",   _gen_realistic_collision,
                   "Hand-picked 'different inmate, same name, close DOB' pairs "
                   "(falsification battery — MUST NOT auto-merge)."),
]
