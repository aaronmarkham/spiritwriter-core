"""Tests for spiritwriter.models.document — document data models.

Knowledge-model coverage (SourceType, KnowledgeSource, KnowledgeGraph,
KnowledgeProject, CrossSourceLink, Note, Connection, generate_id) lives in
test_knowledge_models.py — add new knowledge-model assertions there, not
here, so the two files don't drift.
"""

from spiritwriter.models.document import (
    AtomType,
    DocumentAtom,
    DocumentType,
)


class TestAtomType:
    def test_enum_values(self):
        assert AtomType.TITLE.value == "title"
        assert AtomType.FIGURE.value == "figure"
        assert AtomType.PARAGRAPH.value == "paragraph"

    def test_all_types_have_values(self):
        for t in AtomType:
            assert isinstance(t.value, str)


class TestDocumentAtom:
    def test_creation(self):
        atom = DocumentAtom(
            atom_id="a1",
            atom_type=AtomType.PARAGRAPH,
            content="Some text",
        )
        assert atom.atom_type == AtomType.PARAGRAPH
        assert atom.content == "Some text"
        assert atom.source_page is None

    def test_with_page(self):
        atom = DocumentAtom(
            atom_id="a2",
            atom_type=AtomType.FIGURE,
            content="A figure",
            source_page=3,
        )
        assert atom.source_page == 3


class TestDocumentType:
    def test_enum(self):
        assert DocumentType.SCIENTIFIC_PAPER.value
