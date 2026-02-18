"""Centralized word lists, patterns, and noise filters for content classification.

All stopword sets, institutional patterns, and structural noise terms live here.
Import from this module rather than scattering dictionaries across the codebase.
"""

import re
from typing import List


# ──────────────────────────────────────────────────
# Institutional / organizational patterns
# These identify metadata (who), not content (what)
# ──────────────────────────────────────────────────

INSTITUTION_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"(?:university|universit[ée]|universidad)\s+(?:of\s+)?[\w\s]+",
        r"(?:institute|institut)\s+(?:of|for|de)\s+[\w\s]+",
        r"(?:school|college|faculty|department|dept\.?)\s+of\s+[\w\s]+",
        r"(?:laboratory|lab|centre|center)\s+(?:of|for)\s+[\w\s]+",
        r"[\w\s]+(?:polytechnic|polytechnical)\s+[\w\s]*",
    ]
]

INSTITUTION_INDICATOR_WORDS = frozenset({
    "university", "institute", "school", "college", "department",
    "faculty", "laboratory", "centre", "center", "hospital",
    "corporation", "inc", "ltd", "gmbh", "polytechnic", "polytechnical",
    "academy", "research center", "research centre", "national lab",
})


# ──────────────────────────────────────────────────
# Journal / venue / publisher names
# ──────────────────────────────────────────────────

VENUE_INDICATOR_WORDS = frozenset({
    "journal", "proceedings", "conference", "symposium",
    "transactions", "letters", "ieee", "acm", "springer",
    "workshop", "annual meeting",
})


# ──────────────────────────────────────────────────
# Structural noise — terms that are document structure,
# not semantic content. Should never become topics.
# ──────────────────────────────────────────────────

STRUCTURAL_NOISE_TERMS = frozenset({
    # Block types
    "figure", "figure caption", "caption", "header", "footer",
    "paragraph", "abstract", "section", "subsection", "title",
    "table", "equation", "citation", "reference", "bibliography",
    "author", "date", "keyword", "metadata", "page header", "page footer",
    # Document structure
    "volume information", "mathematical expression", "mathematical bracket",
    "author biography", "affiliations", "contact", "acknowledgments",
    # Generic academic section names
    "introduction", "conclusion", "related work", "background",
    "methodology", "methods", "results", "discussion", "evaluation",
    "experimental", "experiment", "future work",
})


# ──────────────────────────────────────────────────
# Theme stopwords — generic academic terms too broad
# to be meaningful as standalone themes
# ──────────────────────────────────────────────────

THEME_STOPWORDS = frozenset({
    "this", "that", "with", "from", "have", "been", "their", "which",
    "also", "more", "than", "into", "each", "such", "only", "other",
    "some", "these", "those", "over", "many", "most", "both", "does",
    "used", "using", "based", "however", "results", "experimental",
    "international", "introduction", "related", "conclusion", "nature",
    "conference", "proceedings", "references", "abstract", "proposed",
    "machine", "neural", "network", "networks", "learning", "training",
    "computer", "vision", "language", "intelligence", "artificial",
    "analysis", "system", "systems", "performance", "algorithm",
    "knowledge", "information", "processing", "research",
})


# ──────────────────────────────────────────────────
# Utility functions for checking membership
# ──────────────────────────────────────────────────

def is_institutional_name(text: str) -> bool:
    """Check if text is an institutional/organizational name rather than a concept."""
    text_lower = text.lower()
    return any(indicator in text_lower for indicator in INSTITUTION_INDICATOR_WORDS)


def is_venue_name(text: str) -> bool:
    """Check if text is a journal/conference/venue name."""
    text_lower = text.lower()
    return any(w in text_lower for w in VENUE_INDICATOR_WORDS)


def is_structural_noise(text: str) -> bool:
    """Check if text is a structural document term, not content."""
    return text.lower().strip() in STRUCTURAL_NOISE_TERMS


def is_theme_candidate(topic: str) -> bool:
    """Determine if a topic is a legitimate theme vs. metadata noise.

    Rejects institutional names, journal/venue names, and geographic-only terms.
    """
    if is_institutional_name(topic):
        return False
    if is_venue_name(topic):
        return False
    return True
