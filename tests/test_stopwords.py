"""Tests for spiritwriter.stopwords — centralized noise filters."""

from spiritwriter.stopwords import (
    INSTITUTION_PATTERNS,
    INSTITUTION_INDICATOR_WORDS,
    VENUE_INDICATOR_WORDS,
    STRUCTURAL_NOISE_TERMS,
    THEME_STOPWORDS,
    is_institutional_name,
    is_venue_name,
    is_structural_noise,
    is_theme_candidate,
)


class TestInstitutionalName:
    def test_university(self):
        assert is_institutional_name("University of California")

    def test_institute(self):
        assert is_institutional_name("Max Planck Institute for AI")

    def test_hospital(self):
        assert is_institutional_name("Johns Hopkins Hospital")

    def test_not_institution(self):
        assert not is_institutional_name("transformer architecture")

    def test_case_insensitive(self):
        assert is_institutional_name("DEPARTMENT of Physics")


class TestVenueName:
    def test_journal(self):
        assert is_venue_name("Journal of Machine Learning Research")

    def test_conference(self):
        assert is_venue_name("Conference on Computer Vision")

    def test_ieee(self):
        assert is_venue_name("IEEE Transactions on Signal Processing")

    def test_not_venue(self):
        assert not is_venue_name("attention mechanism")


class TestStructuralNoise:
    def test_figure(self):
        assert is_structural_noise("figure")

    def test_abstract(self):
        assert is_structural_noise("abstract")

    def test_whitespace(self):
        assert is_structural_noise("  figure  ")

    def test_case_insensitive(self):
        assert is_structural_noise("Figure")

    def test_not_noise(self):
        assert not is_structural_noise("transformer")


class TestThemeCandidate:
    def test_rejects_institution(self):
        assert not is_theme_candidate("MIT Department of Physics")

    def test_rejects_venue(self):
        assert not is_theme_candidate("IEEE Transactions on AI")

    def test_accepts_real_topic(self):
        assert is_theme_candidate("attention mechanism")

    def test_accepts_domain_topic(self):
        assert is_theme_candidate("protein folding")


class TestConstants:
    def test_institution_patterns_are_compiled(self):
        import re
        for p in INSTITUTION_PATTERNS:
            assert isinstance(p, re.Pattern)

    def test_frozensets_are_frozen(self):
        assert isinstance(INSTITUTION_INDICATOR_WORDS, frozenset)
        assert isinstance(VENUE_INDICATOR_WORDS, frozenset)
        assert isinstance(STRUCTURAL_NOISE_TERMS, frozenset)
        assert isinstance(THEME_STOPWORDS, frozenset)

    def test_stopwords_nonempty(self):
        assert len(THEME_STOPWORDS) > 10
        assert len(STRUCTURAL_NOISE_TERMS) > 10
