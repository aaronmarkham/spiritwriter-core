"""People-corpus domain-specific mutation rules.

Drawn from drift modes observed in frio jail rosters — particularly
Hispanic two-surname patterns that roster systems handle inconsistently.

Each mutation labels the family it belongs to so per-family recall is
attributable in the report.
"""

from __future__ import annotations

from typing import Any

from benchmarks.eval.ess_accuracy.mutations import Mutation, MutationFamily


# ── Diminutive map ──────────────────────────────────────────────────
# Conservative — only well-established Spanish/English diminutives.
# Extend as needed; the report's per-family breakdown will surface gaps.

_DIMINUTIVES: dict[str, list[str]] = {
    "carlos":     ["Carlitos"],
    "juan":       ["Juanito"],
    "jose":       ["Pepe"],
    "miguel":     ["Miguelito"],
    "francisco":  ["Pancho", "Paco"],
    "luis":       ["Luisito"],
    "antonio":    ["Toni"],
    "roberto":    ["Berto"],
    "eduardo":    ["Lalo"],
    "rafael":     ["Rafa"],
    "diego":      ["Dieguito"],
    "andres":     ["Andresito"],
    "sebastian":  ["Sebas"],
    "maria":      ["Mari"],
    "ana":        ["Anita"],
    "sofia":      ["Sofi"],
    "isabella":   ["Isa"],
    "daniela":    ["Dani"],
    "lucia":      ["Lu"],
    "gabriela":   ["Gabi"],
    "valentina":  ["Vale"],
    "camila":     ["Cami"],
    "patricia":   ["Patty", "Pat"],
    "jennifer":   ["Jen"],
    "linda":      ["Lin"],
    "mary":       ["Mary"],
    "robert":     ["Rob", "Bob"],
    "michael":    ["Mike"],
    "james":      ["Jim", "Jimmy"],
    "david":      ["Dave"],
    "patrick":    ["Pat"],
}


# ── Helpers ─────────────────────────────────────────────────────────


def _split_surnames(last_name: str) -> list[str]:
    """Split a multi-part surname on spaces and hyphens.

    "Garcia Lopez" -> ["Garcia", "Lopez"]
    "Smith-Jones"  -> ["Smith", "Jones"]
    "De La Cruz"   -> ["De", "La", "Cruz"]
    """
    parts: list[str] = []
    for chunk in last_name.split():
        parts.extend(p for p in chunk.split("-") if p)
    return parts


def _copy_with_last(record: dict[str, Any], new_last: str) -> dict[str, Any]:
    out = dict(record)
    out["last_name"] = new_last
    return out


def _copy_with_first(record: dict[str, Any], new_first: str) -> dict[str, Any]:
    out = dict(record)
    out["first_name"] = new_first
    return out


# ── Mutation generators ─────────────────────────────────────────────


def _gen_middle_initial_add(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Carlos -> Carlos A (insert random middle initial)."""
    first = record.get("first_name", "")
    if not isinstance(first, str) or not first or " " in first:
        # Already has a middle name; skip
        return []
    new_first = f"{first} A"
    return [Mutation(
        family="middle_initial_add",
        mutated=_copy_with_first(record, new_first),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{first!r} -> {new_first!r}",
    )]


def _gen_middle_initial_drop(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Carlos A -> Carlos (drop trailing single-letter initial)."""
    first = record.get("first_name", "")
    if not isinstance(first, str):
        return []
    parts = first.split()
    if len(parts) < 2 or len(parts[-1]) != 1:
        return []
    new_first = " ".join(parts[:-1])
    return [Mutation(
        family="middle_initial_drop",
        mutated=_copy_with_first(record, new_first),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{first!r} -> {new_first!r}",
    )]


def _gen_diminutive(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Carlos -> Carlitos via the diminutive map."""
    first = record.get("first_name", "")
    if not isinstance(first, str):
        return []
    # Use first token only (e.g. "Jose Luis" -> look up "jose")
    head = first.split()[0].lower() if first.split() else ""
    out: list[Mutation] = []
    for nick in _DIMINUTIVES.get(head, []):
        new_first = first.replace(first.split()[0], nick, 1)
        if new_first == first:
            continue
        out.append(Mutation(
            family="diminutive",
            mutated=_copy_with_first(record, new_first),
            canonical=record,
            same_entity=True,
            expected_tier_min="t3_fuzzy",
            notes=f"{first!r} -> {new_first!r}",
        ))
    return out


def _gen_surname_duplication(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Maria Paten -> Maria Paten Paten.

    Real frio drift: roster systems with a 2-surname field sometimes
    duplicate the single recorded surname into both slots.
    """
    last = record.get("last_name", "")
    if not isinstance(last, str) or not last:
        return []
    parts = _split_surnames(last)
    if len(parts) != 1:
        return []  # only applies to single-surname canonicals
    new_last = f"{last} {last}"
    return [Mutation(
        family="surname_duplication",
        mutated=_copy_with_last(record, new_last),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{last!r} -> {new_last!r}",
    )]


def _gen_surname_hyphenate_duplicate(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Maria Paten -> Maria Paten-Paten (the hyphenated form of duplication)."""
    last = record.get("last_name", "")
    if not isinstance(last, str) or not last:
        return []
    parts = _split_surnames(last)
    if len(parts) != 1:
        return []
    new_last = f"{last}-{last}"
    return [Mutation(
        family="surname_hyphenate_duplicate",
        mutated=_copy_with_last(record, new_last),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{last!r} -> {new_last!r}",
    )]


def _gen_surname_drop_maternal(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Garcia Lopez -> Garcia (drop the maternal/second surname).

    Very common in cross-jurisdictional drift — some rosters keep both,
    some keep only the paternal surname.
    """
    last = record.get("last_name", "")
    if not isinstance(last, str) or not last:
        return []
    parts = _split_surnames(last)
    if len(parts) < 2:
        return []
    new_last = parts[0]
    return [Mutation(
        family="surname_drop_maternal",
        mutated=_copy_with_last(record, new_last),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{last!r} -> {new_last!r}",
    )]


def _gen_surname_hyphenate(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Garcia Lopez -> Garcia-Lopez (space-to-hyphen normalization drift)."""
    last = record.get("last_name", "")
    if not isinstance(last, str) or " " not in last:
        return []
    new_last = last.replace(" ", "-")
    return [Mutation(
        family="surname_hyphenate",
        mutated=_copy_with_last(record, new_last),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{last!r} -> {new_last!r}",
    )]


def _gen_surname_dehyphenate(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Smith-Jones -> Smith Jones (hyphen-to-space drift)."""
    last = record.get("last_name", "")
    if not isinstance(last, str) or "-" not in last:
        return []
    new_last = last.replace("-", " ")
    return [Mutation(
        family="surname_dehyphenate",
        mutated=_copy_with_last(record, new_last),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{last!r} -> {new_last!r}",
    )]


def _gen_four_name_compress(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Jose Luis Hernandez Martinez -> Jose Hernandez.

    Common roster compression: drop the middle given name + maternal
    surname, leaving the bare paternal pair.
    """
    first = record.get("first_name", "")
    last = record.get("last_name", "")
    if not isinstance(first, str) or not isinstance(last, str):
        return []
    given_parts = first.split()
    surname_parts = _split_surnames(last)
    if len(given_parts) < 2 or len(surname_parts) < 2:
        return []
    new_first = given_parts[0]
    new_last = surname_parts[0]
    out = dict(record)
    out["first_name"] = new_first
    out["last_name"] = new_last
    return [Mutation(
        family="four_name_compress",
        mutated=out,
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{first!r} {last!r} -> {new_first!r} {new_last!r}",
    )]


FAMILIES: list[MutationFamily] = [
    MutationFamily("middle_initial_add",       _gen_middle_initial_add,
                   "Insert trailing single-letter middle initial."),
    MutationFamily("middle_initial_drop",      _gen_middle_initial_drop,
                   "Drop trailing single-letter middle initial."),
    MutationFamily("diminutive",               _gen_diminutive,
                   "Replace given name with diminutive (Carlos -> Carlitos)."),
    MutationFamily("surname_duplication",      _gen_surname_duplication,
                   "Maria Paten -> Maria Paten Paten (frio roster artifact)."),
    MutationFamily("surname_hyphenate_duplicate", _gen_surname_hyphenate_duplicate,
                   "Maria Paten -> Maria Paten-Paten."),
    MutationFamily("surname_drop_maternal",    _gen_surname_drop_maternal,
                   "Garcia Lopez -> Garcia (drop second surname)."),
    MutationFamily("surname_hyphenate",        _gen_surname_hyphenate,
                   "Garcia Lopez -> Garcia-Lopez."),
    MutationFamily("surname_dehyphenate",      _gen_surname_dehyphenate,
                   "Smith-Jones -> Smith Jones."),
    MutationFamily("four_name_compress",       _gen_four_name_compress,
                   "Jose Luis Garcia Lopez -> Jose Garcia."),
]
