"""Seed the canonical vocabulary registry.

Populates the terms database with spiritwriter's canonical terminology
plus known drift aliases and explicitly-flagged invented/deferred terms.
Idempotent — running twice is a no-op for existing terms.

Usage::

    python -m spiritwriter.sw_vocab.seed
    python -m spiritwriter.sw_vocab.seed --db-path /path/to/terms.db
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from spiritwriter.fabric.canonicalize import CanonicalRegistry, ResolutionTier

from spiritwriter.sw_vocab.registry import canonical_term_list, load_registry

__all__ = ["bundled_terms", "seed", "main"]


_BUNDLED = Path(__file__).resolve().parent / "data" / "canonical_terms.json"


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


def seed(
    db_path: str | Path | None = None,
    extra: list[dict[str, Any]] | None = None,
    verbose: bool = False,
) -> CanonicalRegistry:
    """Seed the vocabulary registry. Returns the registry."""
    registry = load_registry(db_path)
    terms = bundled_terms()
    if extra:
        terms = terms + list(extra)

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
    args = parser.parse_args()
    seed(args.db_path, verbose=True)


if __name__ == "__main__":
    main()
