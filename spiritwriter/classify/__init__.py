"""Content-aware document classification."""

from .content import (
    ContentClassifier,
    is_institutional_name,
    is_theme_candidate,
    INSTITUTION_PATTERNS,
    INSTITUTION_INDICATOR_WORDS,
    EXTRACTION_RULES,
)

__all__ = [
    "ContentClassifier",
    "is_institutional_name",
    "is_theme_candidate", 
    "INSTITUTION_PATTERNS",
    "INSTITUTION_INDICATOR_WORDS",
    "EXTRACTION_RULES",
]