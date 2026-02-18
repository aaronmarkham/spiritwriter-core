"""Tests for spiritwriter.classify — content classification."""

from spiritwriter.classify import (
    ContentClassifier,
    EXTRACTION_RULES,
    is_theme_candidate,
    is_institutional_name,
    is_venue_name,
    is_structural_noise,
)
from spiritwriter.classify.rules import EXTRACTION_RULES
from spiritwriter.classify.zones import identify_zones
from spiritwriter.models.document import DocumentType


class TestExtractionRules:
    def test_has_generic(self):
        assert DocumentType.GENERIC in EXTRACTION_RULES

    def test_has_scientific_paper(self):
        assert DocumentType.SCIENTIFIC_PAPER in EXTRACTION_RULES

    def test_rules_are_dicts(self):
        for doc_type, rules in EXTRACTION_RULES.items():
            assert isinstance(rules, dict), f"Rules for {doc_type} should be a dict"


class TestContentClassifier:
    def test_instantiation(self):
        clf = ContentClassifier()
        assert clf is not None

    def test_has_classify_method(self):
        clf = ContentClassifier()
        assert callable(getattr(clf, "classify", None))


class TestReexports:
    """Verify __init__.py re-exports work."""
    def test_all_exports(self):
        from spiritwriter import classify
        assert hasattr(classify, "ContentClassifier")
        assert hasattr(classify, "EXTRACTION_RULES")
        assert hasattr(classify, "is_theme_candidate")
