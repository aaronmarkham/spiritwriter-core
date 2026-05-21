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


def _copy_with(record: dict[str, Any], **fields: Any) -> dict[str, Any]:
    out = dict(record)
    out.update(fields)
    return out


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


# ── Falsification battery (added 2026-05-19) ───────────────────────
# These families specifically test claims the campaign needs to defend
# against peer review. dob_typo tests whether typo-shifted same-entity
# records still resolve; realistic_collision tests whether different
# entities sharing 2/3 fields stay separate.


_DATE_ANCHOR = "dob"


def _gen_dob_typo(record: dict[str, Any], ess_fields: list[str]) -> list[Mutation]:
    """Shift DOB by 1 day — models off-by-one data-entry typos.

    Same entity by intent (one person, fat-fingered date). Expected to
    land at T3 (DOB digest differs, but last_name + first_name fuzzy
    are both 1.000). If it lands at T1+T2, the engine is over-merging
    a date that's genuinely different — interesting either way:
    real-world rosters DO contain DOB typos.
    """
    import datetime
    dob = record.get(_DATE_ANCHOR, "")
    if not isinstance(dob, str) or len(dob) != 10:
        return []
    try:
        d = datetime.date.fromisoformat(dob)
        new_d = (d + datetime.timedelta(days=1)).isoformat()
    except (ValueError, OverflowError):
        return []
    return [Mutation(
        family="dob_typo",
        mutated=_copy_with(record, **{_DATE_ANCHOR: new_d}),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{dob!r} -> {new_d!r} (off-by-one-day typo)",
    )]


# Hand-curated collision pairs: real-world plausible "different
# entities that share 2 of 3 ESS fields." Keyed by canonical entity
# fingerprint (last_name|first_name|dob).
#
# Selection criteria:
# - Same name as a canonical in entities.json
# - DOB shifted by months (not days — that's dob_typo territory)
# - Gender same as the canonical (so context overlap still triggers
#   T4 — the engine has every reason to confuse them except DOB)
_PEOPLE_COLLISIONS: dict[str, dict[str, Any]] = {
    "Smith|James|1982-04-14": {
        "last_name": "Smith", "first_name": "James",
        "dob": "1982-08-22", "gender": "M",
    },
    "Johnson|Mary|1976-09-23": {
        "last_name": "Johnson", "first_name": "Mary",
        "dob": "1976-11-30", "gender": "F",
    },
    "Garcia|Carlos|1985-03-12": {
        "last_name": "Garcia", "first_name": "Carlos",
        "dob": "1988-06-04", "gender": "M",
    },
    "Garcia|Maria|1990-06-04": {
        "last_name": "Garcia", "first_name": "Maria",
        "dob": "1990-11-15", "gender": "F",
    },
    "Tanaka|Hiroshi|1985-10-20": {
        "last_name": "Tanaka", "first_name": "Hiroshi",
        "dob": "1985-12-08", "gender": "M",
    },
}


def _gen_realistic_collision(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """Generate a 'genuinely different entity, sharing 2/3 fields' mutation.

    The killer false-merge test. Real-world plausible: two people with
    the same name born in the same year (or close to it). Engine MUST
    NOT auto-merge (T1+T2 = 0).
    """
    key = (
        f"{record.get('last_name','')}|{record.get('first_name','')}"
        f"|{record.get('dob','')}"
    )
    pair = _PEOPLE_COLLISIONS.get(key)
    if pair is None:
        return []
    return [Mutation(
        family="realistic_collision",
        mutated=pair,
        canonical=record,
        same_entity=False,
        expected_tier_min=None,
        notes=(
            f"different entity sharing 2/3 ess_fields: "
            f"dob {record.get('dob')!r} vs {pair['dob']!r}"
        ),
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
    MutationFamily("dob_typo",                 _gen_dob_typo,
                   "Off-by-one-day DOB typo (falsification battery)."),
    MutationFamily("realistic_collision",      _gen_realistic_collision,
                   "Hand-picked 'different person, same name, close DOB' pairs "
                   "(falsification battery — MUST NOT auto-merge)."),
]
