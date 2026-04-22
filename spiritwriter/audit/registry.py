"""Audit finding canonicalization via spiritwriter fabric's CanonicalRegistry.

Defines the audit_finding schema, loads/creates the findings registry,
generates prompt-ready canonical lists, and validates audit reports.

The registry is a shared SQLite store of known tracking/surveillance
SDKs. When an audit agent writes a report, `validate_report` resolves
each finding against the registry and flags:

  - unknown findings (not yet canonicalized)
  - name drift (fuzzy match — rename to canonical)
  - category/risk mismatches (agent disagreed with registry defaults)

Default location: ``~/.spiritwriter/audit/findings.db``. Override by
passing ``db_path`` or by setting the ``SPIRITWRITER_AUDIT_DB`` env var.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from spiritwriter.fabric.canonicalize import (
    CanonicalRegistry,
    CanonicalSchema,
    ResolutionTier,
)


AUDIT_FINDING_SCHEMA = CanonicalSchema(
    name="audit_finding",
    ess_fields=["finding_name"],
    fuzzy_fields={"finding_name": 0.90},
    context_fields=["category", "default_risk", "platform"],
)


def _default_db_path() -> Path:
    override = os.environ.get("SPIRITWRITER_AUDIT_DB")
    if override:
        return Path(override)
    return Path.home() / ".spiritwriter" / "audit" / "findings.db"


def _parse_fields(entity: dict[str, Any]) -> dict[str, Any]:
    """Parse ess_fields from a registry entity (may be JSON string or dict)."""
    raw = entity["ess_fields"]
    return json.loads(raw) if isinstance(raw, str) else raw


def load_registry(db_path: str | Path | None = None) -> CanonicalRegistry:
    """Open or create the findings registry."""
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return CanonicalRegistry(path, AUDIT_FINDING_SCHEMA)


def canonical_finding_list(registry: CanonicalRegistry) -> str:
    """Generate the prompt-ready canonical finding list.

    Each line: ``category / default_risk  finding_name``. Suitable for
    dropping into an audit agent's system prompt.
    """
    lines: list[tuple[str, str, str]] = []
    for entity in registry.entities():
        fields = _parse_fields(entity)
        name = fields.get("finding_name", "?")
        cat = fields.get("category", "unknown")
        risk = fields.get("default_risk", "unknown")
        lines.append((cat, risk, name))

    lines.sort()
    return "\n".join(f"  {cat:20s} / {risk:10s}  {name}" for cat, risk, name in lines)


def validate_report(
    report: dict[str, Any],
    registry: CanonicalRegistry,
) -> list[dict[str, Any]]:
    """Resolve each finding in a report against the canonical registry.

    Returns a list of issues (empty = clean report). Does NOT modify
    the report — issues are informational for human review.

    Issue types:
      - category_mismatch: finding name matches but category differs
      - risk_mismatch: finding name matches but risk differs
      - name_drift: finding name is a fuzzy match, not exact
      - unknown_finding: finding not in registry
    """
    issues: list[dict[str, Any]] = []

    for finding in report.get("findings", []):
        candidate = {"finding_name": finding["name"]}
        result = registry.resolve(candidate)

        if result.tier == ResolutionTier.T1_EXACT:
            entity = registry.get_entity(result.canonical_id)
            if entity is None:
                continue
            stored = _parse_fields(entity)

            canonical_cat = stored.get("category")
            if canonical_cat and finding.get("category") != canonical_cat:
                issues.append(
                    {
                        "finding": finding["name"],
                        "issue": "category_mismatch",
                        "expected": canonical_cat,
                        "got": finding.get("category"),
                    }
                )

            canonical_risk = stored.get("default_risk")
            if canonical_risk and finding.get("risk") != canonical_risk:
                issues.append(
                    {
                        "finding": finding["name"],
                        "issue": "risk_mismatch",
                        "expected": canonical_risk,
                        "got": finding.get("risk"),
                    }
                )

        elif result.tier in (ResolutionTier.T2_STRONG, ResolutionTier.T3_FUZZY):
            entity = registry.get_entity(result.canonical_id)
            if entity is None:
                continue
            stored = _parse_fields(entity)
            issues.append(
                {
                    "finding": finding["name"],
                    "issue": "name_drift",
                    "canonical": stored.get("finding_name"),
                    "confidence": result.confidence,
                    "tier": result.tier.value,
                }
            )

        else:
            issues.append(
                {
                    "finding": finding["name"],
                    "issue": "unknown_finding",
                    "note": "Not in canonical registry. Review for addition.",
                }
            )

    return issues
