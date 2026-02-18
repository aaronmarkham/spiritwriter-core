"""Tests for spiritwriter.kb — knowledge base manager."""

import json
import tempfile
from pathlib import Path

from spiritwriter.kb.manager import (
    resolve_project,
    load_project,
    save_project,
    calculate_topic_quality,
    get_atom_type_distribution,
)
from spiritwriter.models.knowledge import KnowledgeProject


class TestResolveProject:
    def test_exact_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            kb_dir = Path(tmpdir)
            proj_dir = kb_dir / "my-project"
            proj_dir.mkdir()
            (proj_dir / "project.json").write_text("{}")
            result = resolve_project("my-project", kb_dir=kb_dir)
            assert result == proj_dir

    def test_no_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = resolve_project("nope", kb_dir=Path(tmpdir))
            assert result is None


class TestLoadSaveProject:
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj_dir = Path(tmpdir)
            proj = KnowledgeProject(
                project_id="test-123",
                name="Test Project",
                description="A test",
            )
            save_project(proj_dir, proj)
            assert (proj_dir / "project.json").exists()

            loaded = load_project(proj_dir)
            assert loaded.name == "Test Project"
            assert loaded.project_id == "test-123"
            assert loaded.description == "A test"


class TestCalculateTopicQuality:
    def test_with_topics(self):
        topics = {
            "attention": ["atom1", "atom2"],
            "transformer": ["atom1"],
        }
        atom_sources = {"atom1": "doc1", "atom2": "doc1"}
        result = calculate_topic_quality(topics, atom_sources)
        assert isinstance(result, dict)

    def test_empty(self):
        result = calculate_topic_quality({}, {})
        assert isinstance(result, dict)


class TestAtomTypeDistribution:
    def test_empty(self):
        result = get_atom_type_distribution({})
        assert result == {}
