"""Baseline comparators for ESS accuracy.

The named baseline is **Jaccard-on-tokens** — the comparator
cmc-lite-v0.1.md cites at "9–36% match rate." We score under identical
conditions (same corpus, same mutations) so the comparison is apples
to apples.

A Jaccard match is declared if the token overlap between candidate and
canonical is ≥ ``threshold``. Threshold default is 0.80 (strict),
appropriate for structured records.

**Field selection matters.** The CMC-Lite claim was about Jaccard on
free-text atom tokens. Applying it directly to structured records with
strong key anchors (e.g. DOB tokenized as ``1985``, ``03``, ``12``)
makes it trivially permissive — three out of ~5 tokens are anchor
tokens that preserve perfectly across any name mutation. For an honest
name-resolution comparison, pass ``jaccard_fields`` to restrict the
tokenization to the surface-form-varying fields (e.g. just
``last_name`` and ``first_name``).
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokens(record: dict[str, Any], fields: list[str]) -> set[str]:
    """Tokenize concatenated ESS fields into a lowercased set."""
    out: set[str] = set()
    for f in fields:
        v = record.get(f)
        if not isinstance(v, str):
            continue
        out.update(t.lower() for t in _TOKEN_RE.findall(v))
    return out


def jaccard_score(a: dict[str, Any], b: dict[str, Any], fields: list[str]) -> float:
    """Jaccard similarity of token sets across the given fields."""
    ta, tb = _tokens(a, fields), _tokens(b, fields)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def jaccard_matches(
    a: dict[str, Any],
    b: dict[str, Any],
    fields: list[str],
    threshold: float = 0.80,
) -> bool:
    """Does Jaccard-on-tokens consider these the same entity?

    Default threshold 0.80 is strict — appropriate for structured records.
    For free-text atoms, 0.5 is more representative of the original
    CMC-Lite measurement.
    """
    return jaccard_score(a, b, fields) >= threshold
