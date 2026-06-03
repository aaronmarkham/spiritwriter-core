"""Unit tests for Knowledge Project models"""

from spiritwriter.models.knowledge import (
    SourceType,
    KnowledgeSource,
    CrossSourceLink,
    Note,
    Connection,
    KnowledgeGraph,
    KnowledgeProject,
    generate_id,
)
from spiritwriter.models.document import AtomType, DocumentAtom


class TestSourceType:
    """Test SourceType enum"""

    def test_values(self):
        assert SourceType.PAPER.value == "paper"
        assert SourceType.ARTICLE.value == "article"
        assert SourceType.NOTE.value == "note"
        assert SourceType.DATASET.value == "dataset"
        assert SourceType.URL.value == "url"

    def test_from_string(self):
        assert SourceType("paper") == SourceType.PAPER
        assert SourceType("note") == SourceType.NOTE


class TestKnowledgeSource:
    """Test KnowledgeSource dataclass"""

    def test_creation(self):
        source = KnowledgeSource(
            source_id="src_abc123def456",
            source_type=SourceType.PAPER,
            title="Test Paper",
            authors=["Author A"],
            atom_count=50,
        )
        assert source.source_id == "src_abc123def456"
        assert source.source_type == SourceType.PAPER
        assert source.title == "Test Paper"
        assert source.authors == ["Author A"]
        assert source.atom_count == 50
        assert source.tags == []

    def test_to_dict(self):
        source = KnowledgeSource(
            source_id="src_abc",
            source_type=SourceType.ARTICLE,
            title="An Article",
            tags=["ml", "nlp"],
        )
        d = source.to_dict()
        assert d["source_id"] == "src_abc"
        assert d["source_type"] == "article"
        assert d["title"] == "An Article"
        assert d["tags"] == ["ml", "nlp"]

    def test_from_dict(self):
        data = {
            "source_id": "src_xyz",
            "source_type": "paper",
            "title": "From Dict Paper",
            "authors": ["Bob"],
            "added_at": "2026-01-23T10:00:00",
            "atom_count": 100,
            "page_count": 8,
            "figure_count": 3,
        }
        source = KnowledgeSource.from_dict(data)
        assert source.source_id == "src_xyz"
        assert source.source_type == SourceType.PAPER
        assert source.title == "From Dict Paper"
        assert source.atom_count == 100
        assert source.page_count == 8

    def test_roundtrip(self):
        source = KnowledgeSource(
            source_id="src_rt",
            source_type=SourceType.NOTE,
            title="Roundtrip",
            authors=["Alice", "Bob"],
            added_at="2026-01-01",
            source_path="/tmp/test.pdf",
            document_id="doc_123",
            one_sentence="A test source.",
            atom_count=10,
            page_count=2,
            figure_count=1,
            tags=["test"],
            user_notes="Some note",
        )
        restored = KnowledgeSource.from_dict(source.to_dict())
        assert restored.source_id == source.source_id
        assert restored.source_type == source.source_type
        assert restored.title == source.title
        assert restored.authors == source.authors
        assert restored.atom_count == source.atom_count
        assert restored.tags == source.tags
        assert restored.user_notes == source.user_notes


class TestCrossSourceLink:
    """Test CrossSourceLink dataclass"""

    def test_creation(self):
        link = CrossSourceLink(
            link_id="link_001",
            source_atom_id="doc_a_atom_005",
            target_atom_id="doc_b_atom_010",
            source_source_id="src_aaa",
            target_source_id="src_bbb",
            relationship="same_topic",
            confidence=0.8,
        )
        assert link.link_id == "link_001"
        assert link.relationship == "same_topic"
        assert link.confidence == 0.8
        assert link.created_by == "auto"

    def test_to_dict(self):
        link = CrossSourceLink(
            link_id="link_002",
            source_atom_id="a1",
            target_atom_id="b1",
            source_source_id="src_1",
            target_source_id="src_2",
            relationship="contradicts",
        )
        d = link.to_dict()
        assert d["relationship"] == "contradicts"
        assert d["created_by"] == "auto"

    def test_from_dict(self):
        data = {
            "link_id": "link_003",
            "source_atom_id": "a2",
            "target_atom_id": "b2",
            "source_source_id": "src_x",
            "target_source_id": "src_y",
            "relationship": "extends",
            "confidence": 0.9,
            "created_by": "user",
        }
        link = CrossSourceLink.from_dict(data)
        assert link.relationship == "extends"
        assert link.confidence == 0.9
        assert link.created_by == "user"


class TestNote:
    """Test Note dataclass"""

    def test_creation(self):
        note = Note(
            note_id="note_abc",
            title="My Note",
            content="Some observations about the paper.",
        )
        assert note.note_id == "note_abc"
        assert note.title == "My Note"
        assert note.related_sources == []

    def test_roundtrip(self):
        note = Note(
            note_id="note_rt",
            title="Roundtrip Note",
            content="Content here",
            created_at="2026-01-01",
            updated_at="2026-01-02",
            related_sources=["src_1", "src_2"],
            related_atoms=["atom_a", "atom_b"],
            tags=["important"],
        )
        restored = Note.from_dict(note.to_dict())
        assert restored.note_id == note.note_id
        assert restored.title == note.title
        assert restored.related_sources == note.related_sources
        assert restored.tags == note.tags


class TestConnection:
    """Test Connection dataclass"""

    def test_creation(self):
        conn = Connection(
            connection_id="conn_001",
            from_id="atom_a",
            to_id="atom_b",
            label="A builds on B",
            relationship="builds_on",
        )
        assert conn.connection_id == "conn_001"
        assert conn.from_type == "atom"
        assert conn.to_type == "atom"

    def test_roundtrip(self):
        conn = Connection(
            connection_id="conn_rt",
            from_id="src_1",
            to_id="note_1",
            from_type="source",
            to_type="note",
            label="Source informs note",
            relationship="applies_to",
            created_at="2026-01-01",
        )
        restored = Connection.from_dict(conn.to_dict())
        assert restored.from_type == "source"
        assert restored.to_type == "note"
        assert restored.relationship == "applies_to"


class TestKnowledgeGraph:
    """Test KnowledgeGraph dataclass"""

    def _make_atom(self, atom_id: str, topics=None, entities=None):
        return DocumentAtom(
            atom_id=atom_id,
            atom_type=AtomType.PARAGRAPH,
            content=f"Content of {atom_id}",
            topics=topics or [],
            entities=entities or [],
        )

    def test_creation(self):
        graph = KnowledgeGraph(project_id="kb_test")
        assert graph.atom_count == 0
        assert graph.source_count == 0
        assert graph.cross_link_count == 0

    def test_atom_count(self):
        graph = KnowledgeGraph(
            project_id="kb_test",
            atoms={
                "a1": self._make_atom("a1"),
                "a2": self._make_atom("a2"),
            },
        )
        assert graph.atom_count == 2

    def test_source_count(self):
        graph = KnowledgeGraph(
            project_id="kb_test",
            atoms={
                "a1": self._make_atom("a1"),
                "a2": self._make_atom("a2"),
                "b1": self._make_atom("b1"),
            },
            atom_sources={"a1": "src_a", "a2": "src_a", "b1": "src_b"},
        )
        assert graph.source_count == 2

    def test_get_atoms_for_source(self):
        graph = KnowledgeGraph(
            project_id="kb_test",
            atoms={
                "a1": self._make_atom("a1"),
                "a2": self._make_atom("a2"),
                "b1": self._make_atom("b1"),
            },
            atom_sources={"a1": "src_a", "a2": "src_a", "b1": "src_b"},
        )
        src_a_atoms = graph.get_atoms_for_source("src_a")
        assert len(src_a_atoms) == 2
        assert all(a.atom_id.startswith("a") for a in src_a_atoms)

    def test_get_shared_topics(self):
        graph = KnowledgeGraph(
            project_id="kb_test",
            atoms={
                "a1": self._make_atom("a1", topics=["ml"]),
                "b1": self._make_atom("b1", topics=["ml"]),
            },
            atom_sources={"a1": "src_a", "b1": "src_b"},
            topic_index={"ml": ["a1", "b1"], "nlp": ["a1"]},
        )
        shared = graph.get_shared_topics()
        assert "ml" in shared
        assert "nlp" not in shared

    def test_get_shared_entities(self):
        graph = KnowledgeGraph(
            project_id="kb_test",
            atoms={
                "a1": self._make_atom("a1", entities=["BERT"]),
                "b1": self._make_atom("b1", entities=["BERT"]),
            },
            atom_sources={"a1": "src_a", "b1": "src_b"},
            entity_index={"BERT": ["a1", "b1"], "GPT": ["a1"]},
        )
        shared = graph.get_shared_entities()
        assert "BERT" in shared
        assert "GPT" not in shared

    def test_to_dict(self):
        graph = KnowledgeGraph(
            project_id="kb_test",
            atoms={"a1": self._make_atom("a1")},
            atom_sources={"a1": "src_a"},
            key_themes=["machine learning"],
        )
        d = graph.to_dict()
        assert d["project_id"] == "kb_test"
        assert "a1" in d["atoms"]
        assert d["key_themes"] == ["machine learning"]

    def test_from_dict(self):
        data = {
            "project_id": "kb_test",
            "atoms": {
                "a1": {
                    "atom_id": "a1",
                    "atom_type": "paragraph",
                    "content": "Hello",
                    "has_raw_data": False,
                    "topics": ["test"],
                    "entities": ["BERT"],
                    "importance_score": 0.7,
                }
            },
            "atom_sources": {"a1": "src_a"},
            "cross_links": [],
            "topic_index": {"test": ["a1"]},
            "entity_index": {"BERT": ["a1"]},
            "unified_summary": "A test graph",
            "key_themes": ["testing"],
        }
        graph = KnowledgeGraph.from_dict(data)
        assert graph.atom_count == 1
        assert graph.atoms["a1"].content == "Hello"
        assert graph.atoms["a1"].topics == ["test"]
        assert graph.key_themes == ["testing"]

    def test_cross_links(self):
        link = CrossSourceLink(
            link_id="link_1",
            source_atom_id="a1",
            target_atom_id="b1",
            source_source_id="src_a",
            target_source_id="src_b",
            relationship="same_topic",
        )
        graph = KnowledgeGraph(
            project_id="kb_test",
            cross_links=[link],
        )
        assert graph.cross_link_count == 1


class TestKnowledgeProject:
    """Test KnowledgeProject dataclass"""

    def test_creation(self):
        project = KnowledgeProject(
            project_id="kb_test123",
            name="Test Project",
            description="A test",
        )
        assert project.project_id == "kb_test123"
        assert project.name == "Test Project"
        assert project.source_count == 0
        assert project.total_atoms == 0

    def test_add_source(self):
        project = KnowledgeProject(
            project_id="kb_test",
            name="Test",
        )
        source = KnowledgeSource(
            source_id="src_1",
            source_type=SourceType.PAPER,
            title="Paper 1",
            atom_count=50,
            page_count=10,
            figure_count=3,
        )
        project.add_source(source)
        assert project.source_count == 1
        assert project.total_atoms == 50
        assert project.total_figures == 3
        assert project.total_pages == 10
        assert project.updated_at != ""

    def test_add_multiple_sources(self):
        project = KnowledgeProject(project_id="kb_test", name="Test")
        project.add_source(KnowledgeSource(
            source_id="src_1", source_type=SourceType.PAPER,
            atom_count=50, page_count=10, figure_count=3,
        ))
        project.add_source(KnowledgeSource(
            source_id="src_2", source_type=SourceType.PAPER,
            atom_count=30, page_count=5, figure_count=2,
        ))
        assert project.source_count == 2
        assert project.total_atoms == 80
        assert project.total_pages == 15
        assert project.total_figures == 5

    def test_get_source(self):
        project = KnowledgeProject(project_id="kb_test", name="Test")
        source = KnowledgeSource(
            source_id="src_1", source_type=SourceType.PAPER, title="Found"
        )
        project.add_source(source)
        assert project.get_source("src_1").title == "Found"
        assert project.get_source("src_nonexistent") is None

    def test_to_dict(self):
        project = KnowledgeProject(
            project_id="kb_test",
            name="Dict Test",
            description="desc",
            tags=["a", "b"],
        )
        project.add_source(KnowledgeSource(
            source_id="src_1", source_type=SourceType.PAPER,
            title="P1", atom_count=10,
        ))
        d = project.to_dict()
        assert d["project_id"] == "kb_test"
        assert d["name"] == "Dict Test"
        assert "src_1" in d["sources"]
        assert d["sources"]["src_1"]["source_type"] == "paper"
        assert d["total_atoms"] == 10
        assert d["tags"] == ["a", "b"]

    def test_from_dict(self):
        data = {
            "project_id": "kb_from",
            "name": "From Dict",
            "description": "loaded",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-02",
            "sources": {
                "src_a": {
                    "source_id": "src_a",
                    "source_type": "paper",
                    "title": "Paper A",
                    "atom_count": 25,
                }
            },
            "notes": {
                "note_1": {
                    "note_id": "note_1",
                    "title": "A Note",
                    "content": "Content",
                }
            },
            "connections": [
                {
                    "connection_id": "conn_1",
                    "from_id": "a1",
                    "to_id": "b1",
                    "label": "related",
                }
            ],
            "has_knowledge_graph": True,
            "tags": ["test"],
            "total_atoms": 25,
            "total_figures": 2,
            "total_pages": 5,
        }
        project = KnowledgeProject.from_dict(data)
        assert project.name == "From Dict"
        assert project.source_count == 1
        assert project.get_source("src_a").title == "Paper A"
        assert "note_1" in project.notes
        assert len(project.connections) == 1
        assert project.has_knowledge_graph is True

    def test_roundtrip(self):
        project = KnowledgeProject(
            project_id="kb_rt",
            name="Roundtrip",
            description="Full test",
            created_at="2026-01-01",
            tags=["x", "y"],
        )
        project.add_source(KnowledgeSource(
            source_id="src_1", source_type=SourceType.PAPER,
            title="P1", authors=["Alice"],
            atom_count=40, page_count=8, figure_count=4,
        ))
        project.notes["note_1"] = Note(
            note_id="note_1", title="N1", content="Text"
        )
        project.connections.append(Connection(
            connection_id="conn_1", from_id="a", to_id="b", label="link"
        ))

        restored = KnowledgeProject.from_dict(project.to_dict())
        assert restored.project_id == project.project_id
        assert restored.name == project.name
        assert restored.source_count == 1
        assert restored.total_atoms == 40
        assert "note_1" in restored.notes
        assert len(restored.connections) == 1


class TestGenerateId:
    """Test ID generation utility"""

    def test_format(self):
        id_ = generate_id("kb", "test seed")
        assert id_.startswith("kb_")
        assert len(id_) == 15  # "kb_" + 12 hex chars

    def test_deterministic(self):
        a = generate_id("src", "same seed")
        b = generate_id("src", "same seed")
        assert a == b

    def test_different_seeds(self):
        a = generate_id("kb", "seed_a")
        b = generate_id("kb", "seed_b")
        assert a != b

    def test_different_prefixes(self):
        a = generate_id("kb", "same")
        b = generate_id("src", "same")
        assert a != b
        assert a[3:] == b[4:]  # Same hash, different prefix
