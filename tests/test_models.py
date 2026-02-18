"""Tests for spiritwriter.models — data models."""

from spiritwriter.models.document import (
    AtomType,
    DocumentAtom,
    DocumentType,
    ZoneRole,
    DocumentZone,
    ContentProfile,
    DocumentGraph,
)
from spiritwriter.models.knowledge import (
    KnowledgeProject,
    KnowledgeSource,
    SourceType,
    KnowledgeGraph,
    Note,
    Connection,
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


class TestKnowledgeProject:
    def test_creation(self):
        proj = KnowledgeProject(project_id="p1", name="test-project")
        assert proj.name == "test-project"
        assert proj.sources == {}

    def test_add_source(self):
        proj = KnowledgeProject(project_id="p1", name="test")
        src = KnowledgeSource(
            source_id="s1",
            source_type=SourceType.PAPER,
        )
        proj.sources["s1"] = src
        assert len(proj.sources) == 1


class TestKnowledgeGraph:
    def test_creation(self):
        graph = KnowledgeGraph(project_id="p1")
        assert graph.atoms == {}


class TestSourceType:
    def test_paper(self):
        assert SourceType.PAPER.value == "paper"

    def test_all_types(self):
        for t in SourceType:
            assert isinstance(t.value, str)
