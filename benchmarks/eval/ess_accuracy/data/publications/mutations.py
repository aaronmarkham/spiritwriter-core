"""publications — academic paper drift modes.

Surface drifts that show up across citation styles, reading lists,
and bibliographic data sources. Pairs with the universal mutation
families to give a per-family characterization for this schema shape.
"""

from __future__ import annotations

from typing import Any

from benchmarks.eval.ess_accuracy.mutations import Mutation, MutationFamily


def _copy_with(record: dict[str, Any], **fields: Any) -> dict[str, Any]:
    out = dict(record)
    out.update(fields)
    return out


def _gen_title_subtitle_drop(record, ess_fields):
    """BERT: Pre-training of Deep Bidirectional Transformers ... -> BERT.

    Very common citation drift — abbreviated reading lists, blog posts,
    and shorthand references drop the subtitle, leaving just the
    acronym/short-name preceding the colon.
    """
    title = record.get("title", "")
    if not isinstance(title, str) or ":" not in title:
        return []
    short = title.split(":", 1)[0].strip()
    if not short or short == title:
        return []
    return [Mutation(
        family="title_subtitle_drop",
        mutated=_copy_with(record, title=short),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{title!r} -> {short!r}",
    )]


def _gen_title_subtitle_add(record, ess_fields):
    """Inverse of subtitle drop: short form -> long form.

    For titles without a colon, append a plausible subtitle from the
    short title itself. Skipped if title is already long.
    """
    title = record.get("title", "")
    if not isinstance(title, str) or ":" in title or len(title) > 50:
        return []
    new_title = f"{title}: A Comprehensive Study"
    return [Mutation(
        family="title_subtitle_add",
        mutated=_copy_with(record, title=new_title),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{title!r} -> {new_title!r}",
    )]


def _gen_year_missing(record, ess_fields):
    """Drop the year field entirely — some bibliographic records omit it.

    Year is in the ESS, so omitting it changes the ESS digest. T1 misses;
    title + first_author_last fuzzy might rescue if they pass thresholds.
    Realistic drift in informal citation contexts.
    """
    if not record.get("year"):
        return []
    new = dict(record)
    new.pop("year", None)
    return [Mutation(
        family="year_missing",
        mutated=new,
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"year {record['year']!r} dropped",
    )]


def _gen_first_author_initial(record, ess_fields):
    """Devlin -> J. Devlin (some cite styles include initial in surname field).

    Not strictly realistic for most bibliographic records (surname and
    given-name are usually separate), but tests fuzzy resilience on
    the surname field when initials sneak in.
    """
    last = record.get("first_author_last", "")
    if not isinstance(last, str) or " " in last or "." in last:
        return []
    new_last = f"J. {last}"
    return [Mutation(
        family="first_author_initial",
        mutated=_copy_with(record, first_author_last=new_last),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"{last!r} -> {new_last!r}",
    )]


FAMILIES: list[MutationFamily] = [
    MutationFamily("title_subtitle_drop",   _gen_title_subtitle_drop,
                   "Drop subtitle after colon: 'BERT: Pre-training of...' -> 'BERT'."),
    MutationFamily("title_subtitle_add",    _gen_title_subtitle_add,
                   "Add plausible subtitle to short titles."),
    MutationFamily("year_missing",          _gen_year_missing,
                   "Drop the year field (informal citation drift)."),
    MutationFamily("first_author_initial",  _gen_first_author_initial,
                   "Devlin -> J. Devlin (initial in surname field)."),
]
