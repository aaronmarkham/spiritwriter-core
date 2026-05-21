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


# ── Falsification battery ─────────────────────────────────────────


def _gen_year_typo(record: dict[str, Any], ess_fields: list[str]) -> list[Mutation]:
    """Shift year by 1 — off-by-one citation typo (BERT cited as 2017 vs 2018).

    Same paper by intent. Expected to land at T3 if title + author
    fuzzy stays strong, NO_MATCH otherwise. If it lands at T1+T2 the
    engine is over-merging across different publication years —
    informational, since wrong-year citations are a real bibliographic
    drift mode.
    """
    year = record.get("year")
    if not year:
        return []
    try:
        new_year = str(int(year) - 1)
    except (TypeError, ValueError):
        return []
    return [Mutation(
        family="year_typo",
        mutated=_copy_with(record, year=new_year),
        canonical=record,
        same_entity=True,
        expected_tier_min="t3_fuzzy",
        notes=f"year {year!r} -> {new_year!r}",
    )]


# Hand-curated collision pairs: hypothetical-but-plausible papers by
# the same first-author surname in the same year as a canonical entry
# but with a different title. Real-world realistic for bibliographic
# data with only last-name authors (e.g., "Yinhan Liu, RoBERTa 2019"
# vs "Weijie Liu, FastBERT 2020" — both "Liu" in our schema). Keyed
# by canonical fingerprint (title|first_author_last|year).
_PUBLICATION_COLLISIONS: dict[str, dict[str, Any]] = {
    # Liu (Yinhan, RoBERTa 2019) vs hypothetical Liu (different author, same year)
    "RoBERTa: A Robustly Optimized BERT Pretraining Approach|Liu|2019": {
        "title": "Multi-Task Deep Neural Networks for Natural Language Understanding",
        "first_author_last": "Liu",
        "year": "2019",
        "venue": "ACL",
    },
    # Zhang (EDC 2024) vs hypothetical Zhang (different author, same year)
    "Extract, Define, Canonicalize: An LLM-based Framework for Knowledge Graph Construction|Zhang|2024": {
        "title": "RAFT: Adapting Language Model to Domain Specific RAG",
        "first_author_last": "Zhang",
        "year": "2024",
        "venue": "COLM",
    },
    # Brown (GPT-3 2020) vs hypothetical Brown (different author, same year)
    "Language Models are Few-Shot Learners|Brown|2020": {
        "title": "Big Self-Supervised Models are Strong Semi-Supervised Learners",
        "first_author_last": "Brown",
        "year": "2020",
        "venue": "NeurIPS",
    },
    # Wang (Voyager 2023) vs hypothetical Wang (different author, same year)
    "Voyager: An Open-Ended Embodied Agent with Large Language Models|Wang|2023": {
        "title": "Self-Consistency Improves Chain of Thought Reasoning in Language Models",
        "first_author_last": "Wang",
        "year": "2023",
        "venue": "ICLR",
    },
}


def _gen_realistic_collision(
    record: dict[str, Any], ess_fields: list[str]
) -> list[Mutation]:
    """'Different paper, same first_author + year' — MUST NOT auto-merge.

    Real-world risk for bibliographic data with surname-only authors:
    two distinct authors with the same surname publishing in the same
    year produce records that share 2/3 ESS fields (first_author_last
    and year) and differ only in title. Engine MUST distinguish.
    """
    key = (
        f"{record.get('title','')}|{record.get('first_author_last','')}"
        f"|{record.get('year','')}"
    )
    pair = _PUBLICATION_COLLISIONS.get(key)
    if pair is None:
        return []
    return [Mutation(
        family="realistic_collision",
        mutated=pair,
        canonical=record,
        same_entity=False,
        expected_tier_min=None,
        notes=(
            f"different paper sharing first_author_last + year: "
            f"title {record.get('title')[:40]!r}... vs {pair['title'][:40]!r}..."
        ),
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
    MutationFamily("year_typo",             _gen_year_typo,
                   "Off-by-one year typo (falsification battery)."),
    MutationFamily("realistic_collision",   _gen_realistic_collision,
                   "Hand-picked 'different paper, same first_author_last + year' "
                   "pairs (falsification battery — MUST NOT auto-merge)."),
]
