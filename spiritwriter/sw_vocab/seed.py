"""Seed the canonical vocabulary registry.

Populates the terms database with spiritwriter's canonical terminology
plus known drift aliases and explicitly-flagged invented/deferred terms.

**Idempotence semantics.** Re-running ``seed`` adds new canonicals and
silently skips terms whose ``term`` field already exists in the DB. By
itself that means *metadata edits* (new aliases on an existing term,
updated definitions, fixed ``defined_in`` paths) are NOT picked up.
``seed`` detects that mismatch and exits with a clear error pointing
you at ``--force``, which wipes and rebuilds the DB. So:

- Adding a brand-new canonical → just re-seed.
- Editing aliases / definition / category on an existing canonical →
  re-seed with ``--force``.
- Removing a canonical from the JSON → re-seed with ``--force``.

Usage::

    python -m spiritwriter.sw_vocab.seed
    python -m spiritwriter.sw_vocab.seed --db-path /path/to/terms.db
    python -m spiritwriter.sw_vocab.seed --force        # rebuild from scratch
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from spiritwriter.fabric.canonicalize import CanonicalRegistry, ResolutionTier

from spiritwriter.sw_vocab.registry import (
    _build_alias_index,
    _default_db_path,
    _parse_aliases,
    _parse_fields,
    canonical_term_list,
    load_registry,
)

__all__ = ["bundled_terms", "seed", "StaleMetadataError", "main"]


_BUNDLED = Path(__file__).resolve().parent / "data" / "canonical_terms.json"


# Categories that REQUIRE a prefix on the term field (human-readability
# when grepping the seed JSON). Mirrors the test_invented_entries_use_*
# invariant — enforcing it at seed time catches typos before they hit
# the DB.
_CATEGORY_PREFIX = {
    "invented": "INVENTED:",
    "deferred": "DEFERRED:",
}


class StaleMetadataError(RuntimeError):
    """Existing DB has different metadata than the JSON for one or more terms.

    Raised by ``seed()`` when an in-place edit (alias / definition / etc.)
    won't be applied because the term already exists. The fix is to
    re-seed with ``force=True`` (CLI: ``--force``), which wipes the DB
    and rebuilds it from the JSON.
    """


def bundled_terms() -> list[dict[str, Any]]:
    """Return the bundled canonical term list."""
    return json.loads(_BUNDLED.read_text(encoding="utf-8"))


def _prepare_candidate(term: dict[str, Any]) -> dict[str, Any]:
    """Coerce the JSON term entry into a registry candidate.

    `aliases` is stored as a JSON string so it round-trips through the
    registry's str() coercion in metadata_fields.
    """
    candidate = dict(term)
    aliases = candidate.get("aliases", [])
    candidate["aliases"] = json.dumps(aliases, sort_keys=True)
    return candidate


def _check_prefix_convention(terms: list[dict[str, Any]]) -> list[str]:
    """Return per-term errors where category prefix is missing."""
    errors: list[str] = []
    for t in terms:
        cat = t.get("category")
        term_name = t.get("term", "")
        expected_prefix = _CATEGORY_PREFIX.get(cat)
        if expected_prefix and not term_name.startswith(expected_prefix):
            errors.append(
                f"category={cat!r} term {term_name!r} must start with "
                f"{expected_prefix!r}"
            )
    return errors


def _check_for_stale_metadata(
    registry: CanonicalRegistry, terms: list[dict[str, Any]]
) -> list[str]:
    """For each term, compare stored metadata against the JSON.

    Returns human-readable diff messages — empty list = registry agrees
    with the JSON.
    """
    diffs: list[str] = []
    for term in terms:
        candidate = _prepare_candidate(term)
        result = registry.resolve(candidate)
        if result.tier != ResolutionTier.T1_EXACT or not result.canonical_id:
            continue
        existing = registry.get_entity(result.canonical_id)
        if existing is None:
            continue
        stored = _parse_fields(existing)
        for field_name in ("category", "definition", "defined_in", "abbreviation"):
            json_val = candidate.get(field_name)
            db_val = stored.get(field_name)
            if json_val is not None and str(json_val) != str(db_val or ""):
                diffs.append(
                    f"{term['term']!r}: {field_name} JSON={json_val!r} "
                    f"DB={db_val!r}"
                )
        json_aliases = sorted(term.get("aliases", []))
        db_aliases = sorted(_parse_aliases(stored))
        if json_aliases != db_aliases:
            diffs.append(
                f"{term['term']!r}: aliases JSON={json_aliases} DB={db_aliases}"
            )
    return diffs


def _wipe_db(db_path: Path) -> None:
    """Delete the DB and its SQLite sidecars so seed() builds it fresh."""
    for suffix in ("", "-wal", "-shm"):
        candidate = db_path.with_name(db_path.name + suffix)
        if candidate.exists():
            candidate.unlink()


def seed(
    db_path: str | Path | None = None,
    extra: list[dict[str, Any]] | None = None,
    verbose: bool = False,
    *,
    force: bool = False,
) -> CanonicalRegistry:
    """Seed the vocabulary registry. Returns the registry.

    ``force=True`` wipes the DB before seeding — required to pick up
    in-place edits to existing terms (new aliases, definition changes,
    category reclassifications). Without ``force``, ``seed`` detects
    stale metadata and raises ``StaleMetadataError`` rather than
    silently no-op'ing the edit.
    """
    terms = bundled_terms()
    if extra:
        terms = terms + list(extra)

    # Convention check: catch missing INVENTED:/DEFERRED: prefixes at
    # load time, not just in the test suite.
    prefix_errors = _check_prefix_convention(terms)
    if prefix_errors:
        raise ValueError(
            "Term-prefix convention violations:\n  " + "\n  ".join(prefix_errors)
        )

    resolved_path = Path(db_path) if db_path else _default_db_path()

    if force and resolved_path.exists():
        _wipe_db(resolved_path)

    registry = load_registry(db_path)

    # Stale-metadata check: warn if the JSON has changed for any
    # existing term, since plain re-seed won't apply those edits.
    if not force:
        stale = _check_for_stale_metadata(registry, terms)
        if stale:
            registry.close()
            raise StaleMetadataError(
                "Seed JSON has edits to existing terms that would be lost on a "
                "no-force re-seed (T1_EXACT skips the upsert):\n  "
                + "\n  ".join(stale)
                + "\n\nRe-run with force=True (CLI: --force) to wipe and rebuild."
            )

    seeded = 0
    skipped = 0
    for term in terms:
        candidate = _prepare_candidate(term)
        result = registry.resolve(candidate)
        if result.tier == ResolutionTier.T1_EXACT:
            skipped += 1
            continue
        registry.upsert(
            candidate,
            result,
            source_name="seed",
            source_id=f"seed:{term['term']}",
        )
        seeded += 1

    if verbose:
        print(f"Seeded {seeded} terms, skipped {skipped} (already exist)")
        print(f"Registry: {registry.db_path}")
        print(f"\nCanonical term list:\n{canonical_term_list(registry)}")
    return registry


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the vocabulary registry")
    parser.add_argument("--db-path", type=str, default=None, help="Path to terms.db")
    parser.add_argument(
        "--force", action="store_true",
        help="Wipe the existing DB and rebuild from the JSON. Required to "
             "pick up in-place edits (new aliases, updated definitions).",
    )
    args = parser.parse_args()
    try:
        seed(args.db_path, verbose=True, force=args.force)
    except StaleMetadataError as e:
        print(f"\nERROR: {e}", flush=True)
        raise SystemExit(2)
    except ValueError as e:
        print(f"\nERROR: {e}", flush=True)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
