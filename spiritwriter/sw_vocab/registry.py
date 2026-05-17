"""Spiritwriter terminology canonicalization via CanonicalRegistry.

Dogfoods spiritwriter.fabric.canonicalize for our own vocabulary —
so AI agents writing about spiritwriter can't silently drift terms
("Entity Sense Signature" → "Entity Semantic Scoring") without the
drift surfacing as a validation issue.

The registry stores canonical terms (e.g. "Entity Sense Signature"),
their definitions, and known drift aliases. Validation against a
document body or a list of candidate terms returns:

  - unknown_term: candidate is not in the registry; review for addition
  - known_drift: candidate matches a recorded alias of a canonical term
  - fuzzy_drift: candidate is a near-miss to a canonical term (T2/T3)
  - invented_term: candidate matches a term explicitly marked invented
                   (e.g. "SW-CAP" — hallucinated by an LLM)
  - deferred_term: candidate matches a term explicitly marked deferred
                   (e.g. "trust epochs" — listed in docs as future work
                   but NOT implemented)

Default location: ``~/.spiritwriter/sw-vocab/terms.db``. Override by
passing ``db_path`` or by setting the ``SPIRITWRITER_SW_VOCAB_DB`` env var.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from spiritwriter.fabric.canonicalize import (
    CanonicalRegistry,
    CanonicalSchema,
    ResolutionTier,
)


VOCAB_TERM_SCHEMA = CanonicalSchema(
    name="vocab_term",
    ess_fields=["term"],
    fuzzy_fields={"term": 0.85},
    context_fields=["category"],
    metadata_fields=["definition", "defined_in", "abbreviation", "aliases"],
)


def _default_db_path() -> Path:
    override = os.environ.get("SPIRITWRITER_SW_VOCAB_DB")
    if override:
        return Path(override)
    return Path.home() / ".spiritwriter" / "sw-vocab" / "terms.db"


def _parse_fields(entity: dict[str, Any]) -> dict[str, Any]:
    """Parse stored fields from a registry entity (JSON string or dict)."""
    raw = entity["ess_fields"]
    return json.loads(raw) if isinstance(raw, str) else raw


def _parse_aliases(fields: dict[str, Any]) -> list[str]:
    """Aliases are JSON-encoded on store (the registry coerces to str)."""
    raw = fields.get("aliases")
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def load_registry(db_path: str | Path | None = None) -> CanonicalRegistry:
    """Open or create the vocabulary registry."""
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return CanonicalRegistry(path, VOCAB_TERM_SCHEMA)


def canonical_term_list(registry: CanonicalRegistry) -> str:
    """Generate the prompt-ready canonical term list.

    Each line: ``category / term — definition``. Suitable for dropping
    into an AI agent's system prompt as terminology guidance.
    Invented/deferred entries are listed first so the agent knows what
    NOT to write.
    """
    items: list[tuple[str, str, str, str]] = []
    for entity in registry.entities():
        fields = _parse_fields(entity)
        term = fields.get("term", "?")
        cat = fields.get("category", "unknown")
        defn = fields.get("definition", "")
        # Sort key: invented/deferred first, then alphabetical by category, term
        sort_prefix = {
            "invented": "0",
            "deferred": "1",
        }.get(cat, "2")
        items.append((sort_prefix + cat, term, cat, defn))

    items.sort()
    return "\n".join(f"  [{cat:9s}] {term} — {defn}" for _, term, cat, defn in items)


def _build_alias_index(registry: CanonicalRegistry) -> dict[str, dict[str, Any]]:
    """Map lowercased alias → {canonical_term, category, definition}.

    Built once per validation pass. Aliases include the canonical term
    itself (case-insensitive) plus every recorded alias.
    """
    index: dict[str, dict[str, Any]] = {}
    for entity in registry.entities():
        fields = _parse_fields(entity)
        canonical = fields.get("term", "")
        cat = fields.get("category", "unknown")
        defn = fields.get("definition", "")
        record = {
            "canonical": canonical,
            "category": cat,
            "definition": defn,
        }
        # The canonical term itself (case-insensitive surface form)
        if canonical:
            index[canonical.lower()] = record
        # Plus every recorded alias
        for alias in _parse_aliases(fields):
            if alias:
                index[alias.lower()] = record
    return index


def validate_candidate(
    candidate_term: str,
    registry: CanonicalRegistry,
    alias_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Resolve a single candidate term against the registry.

    Returns None if the candidate is clean (exact match to a canonical
    term that is NOT marked invented/deferred). Otherwise returns an
    issue dict. Pass a pre-built alias_index to avoid rebuilding it on
    every call.
    """
    if alias_index is None:
        alias_index = _build_alias_index(registry)

    # Exact / alias match — fastest path, covers known drift
    hit = alias_index.get(candidate_term.lower())
    if hit is not None:
        cat = hit["category"]
        if cat == "invented":
            return {
                "term": candidate_term,
                "issue": "invented_term",
                "canonical": hit["canonical"],
                "note": hit["definition"],
            }
        if cat == "deferred":
            return {
                "term": candidate_term,
                "issue": "deferred_term",
                "canonical": hit["canonical"],
                "note": hit["definition"],
            }
        if hit["canonical"].lower() != candidate_term.lower():
            return {
                "term": candidate_term,
                "issue": "known_drift",
                "canonical": hit["canonical"],
                "note": hit["definition"],
            }
        # Exact match to a real canonical term — clean
        return None

    # Fuzzy fallback — catches drift we haven't recorded as an alias yet.
    # If the fuzzy hit lands on an invented/deferred term, escalate to that
    # category rather than reporting it as generic fuzzy_drift.
    result = registry.resolve({"term": candidate_term})
    if result.tier in (ResolutionTier.T1_EXACT, ResolutionTier.T2_STRONG, ResolutionTier.T3_FUZZY):
        entity = registry.get_entity(result.canonical_id)
        if entity is not None:
            stored = _parse_fields(entity)
            stored_cat = stored.get("category")
            if stored_cat == "invented":
                return {
                    "term": candidate_term,
                    "issue": "invented_term",
                    "canonical": stored.get("term"),
                    "note": stored.get("definition", ""),
                }
            if stored_cat == "deferred":
                return {
                    "term": candidate_term,
                    "issue": "deferred_term",
                    "canonical": stored.get("term"),
                    "note": stored.get("definition", ""),
                }
            return {
                "term": candidate_term,
                "issue": "fuzzy_drift",
                "canonical": stored.get("term"),
                "confidence": result.confidence,
                "tier": result.tier.value,
            }

    return {
        "term": candidate_term,
        "issue": "unknown_term",
        "note": "Not in canonical registry. Review for addition.",
    }


# Markdown extraction patterns. Conservative — we want signal, not noise.
# We look for:
#   1. Bolded short phrases:        **Term**
#   2. Inline code identifiers:     `Term`
#   3. Explicit definitions:        **Term** —  (em-dash definition pattern)
#
# We deliberately do NOT scan free-form prose — too many false positives
# would drown the real drift signals.
_BOLD_PATTERN = re.compile(r"\*\*([A-Za-z][A-Za-z0-9 _.\-]{2,60})\*\*")
_CODE_PATTERN = re.compile(r"`([A-Za-z][A-Za-z0-9_.\-]{2,60})`")


def extract_candidates(text: str) -> list[str]:
    """Extract candidate terminology phrases from markdown/text.

    Looks for bolded phrases and inline code identifiers. De-duplicates
    while preserving first-appearance order.
    """
    seen: set[str] = set()
    out: list[str] = []
    for pat in (_BOLD_PATTERN, _CODE_PATTERN):
        for m in pat.finditer(text):
            term = m.group(1).strip()
            key = term.lower()
            if key not in seen:
                seen.add(key)
                out.append(term)
    return out


_OPT_OUT_PATTERN = re.compile(
    r"<!--\s*vocab:\s*(allow-invented|allow-deferred|allow-all|disable)\s*-->",
    re.IGNORECASE,
)


def _read_opt_outs(text: str) -> set[str]:
    """Scan the document for vocab directives in HTML comments.

    Supported markers (place near the top of the file):

      <!-- vocab: allow-invented -->   skip substring scan for invented terms
      <!-- vocab: allow-deferred -->   skip substring scan for deferred terms
      <!-- vocab: allow-all      -->   skip both
      <!-- vocab: disable        -->   skip everything (treat doc as exempt)

    Bolded / inline-code candidates are still validated unless `disable`.
    Multiple markers may coexist; their effects union.
    """
    out: set[str] = set()
    for m in _OPT_OUT_PATTERN.finditer(text):
        directive = m.group(1).lower()
        if directive == "allow-all":
            out.update({"invented", "deferred"})
        elif directive == "disable":
            out.add("disable")
        elif directive == "allow-invented":
            out.add("invented")
        elif directive == "allow-deferred":
            out.add("deferred")
    return out


def validate_text(
    text: str,
    registry: CanonicalRegistry,
    *,
    include_alias_scan: bool = True,
) -> list[dict[str, Any]]:
    """Validate every candidate term in a body of text.

    Returns a list of issues (empty = clean). Pass ``include_alias_scan``
    False to skip the substring scan for known invented/deferred terms
    (faster but less thorough — useful when you only want to validate
    *marked* terminology and not free-form prose).

    Documents that legitimately discuss the invented/deferred lexicon
    (this SKILL doc, governance specs, the substrate flavor doc that
    lists deferred features) can opt out per-category with HTML-comment
    directives — see ``_read_opt_outs``.
    """
    opt_outs = _read_opt_outs(text)
    if "disable" in opt_outs:
        return []

    alias_index = _build_alias_index(registry)
    issues: list[dict[str, Any]] = []
    seen_drift: set[tuple[str, str]] = set()

    # First pass: bolded + inline-code candidates
    for candidate in extract_candidates(text):
        issue = validate_candidate(candidate, registry, alias_index)
        if issue is None:
            continue
        # Respect opt-outs: if a doc allows mentions of a category, don't
        # flag marked candidates from that category either.
        if issue["issue"] == "invented_term" and "invented" in opt_outs:
            continue
        if issue["issue"] == "deferred_term" and "deferred" in opt_outs:
            continue
        key = (issue["issue"], issue["term"].lower())
        if key not in seen_drift:
            seen_drift.add(key)
            issues.append(issue)

    # Second pass: substring scan for invented + deferred terms anywhere
    # in the text. These are the dangerous categories — if "SW-CAP" or
    # "trust epochs" appears anywhere in prose, we want it flagged.
    # Skip categories the doc has opted out of.
    if include_alias_scan:
        for alias, record in alias_index.items():
            cat = record["category"]
            if cat not in ("invented", "deferred"):
                continue
            if cat in opt_outs:
                continue
            # Word-boundary substring match (avoid e.g. "cap" matching "capture")
            pattern = re.compile(r"\b" + re.escape(alias) + r"\b", re.IGNORECASE)
            if pattern.search(text):
                issue_type = "invented_term" if cat == "invented" else "deferred_term"
                key = (issue_type, alias)
                if key not in seen_drift:
                    seen_drift.add(key)
                    issues.append({
                        "term": alias,
                        "issue": issue_type,
                        "canonical": record["canonical"],
                        "note": record["definition"],
                    })

    return issues


def validate_doc(
    path: str | Path,
    registry: CanonicalRegistry,
) -> list[dict[str, Any]]:
    """Validate a markdown/text file. Returns the issue list."""
    return validate_text(Path(path).read_text(encoding="utf-8"), registry)
