"""Seed the canonical audit findings registry.

Populates the findings database with known tracking, analytics, ads, and
surveillance SDKs from the bundled canonical list. Idempotent — running
twice is a no-op for existing findings.

The bundled list (``spiritwriter/audit/data/canonical_findings.json``)
covers SDKs observed across Android apps audited with this tool. Add
your own by editing that file and re-seeding, or pass ``extra=...``
programmatically.

Usage::

    python -m spiritwriter.audit.seed
    python -m spiritwriter.audit.seed --db-path /path/to/findings.db
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from spiritwriter.fabric.canonicalize import CanonicalRegistry, ResolutionTier

from spiritwriter.audit.registry import canonical_finding_list, load_registry

__all__ = ["bundled_findings", "seed", "main"]


_BUNDLED = Path(__file__).resolve().parent / "data" / "canonical_findings.json"


def bundled_findings() -> list[dict[str, Any]]:
    """Return the bundled canonical finding list."""
    return json.loads(_BUNDLED.read_text(encoding="utf-8"))


def seed(
    db_path: str | Path | None = None,
    extra: list[dict[str, Any]] | None = None,
    verbose: bool = False,
) -> CanonicalRegistry:
    """Seed the findings registry. Returns the registry.

    Silent by default; pass ``verbose=True`` to print a summary + the
    canonical list (the CLI entry point does this).
    """
    registry = load_registry(db_path)
    findings = bundled_findings()
    if extra:
        findings = findings + list(extra)

    seeded = 0
    skipped = 0
    for finding in findings:
        result = registry.resolve(finding)
        if result.tier == ResolutionTier.T1_EXACT:
            skipped += 1
            continue
        registry.upsert(
            finding,
            result,
            source_name="seed",
            source_id=f"seed:{finding['finding_name']}",
        )
        seeded += 1

    if verbose:
        print(f"Seeded {seeded} findings, skipped {skipped} (already exist)")
        print(f"Registry: {registry.db_path}")
        print(f"\nCanonical finding list:\n{canonical_finding_list(registry)}")
    return registry


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the audit findings registry")
    parser.add_argument("--db-path", type=str, default=None, help="Path to findings.db")
    args = parser.parse_args()
    seed(args.db_path, verbose=True)


if __name__ == "__main__":
    main()
