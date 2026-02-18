"""Knowledge base management operations."""

from .manager import (
    KnowledgeBaseManager,
    resolve_project,
    load_project,
    save_project,
    rebuild_knowledge_graph,
    build_concept_from_kb,
    calculate_topic_quality,
    calculate_entity_quality,
    get_atom_type_distribution,
)

# Convenience re-exports
from spiritwriter.models.knowledge import (
    KnowledgeProject,
    KnowledgeGraph,
    KnowledgeSource,
)
from spiritwriter.stopwords import STRUCTURAL_NOISE_TERMS

__all__ = [
    "KnowledgeBaseManager",
    "KnowledgeProject",
    "KnowledgeGraph",
    "KnowledgeSource",
    "resolve_project",
    "load_project",
    "save_project",
    "rebuild_knowledge_graph",
    "build_concept_from_kb",
    "calculate_topic_quality",
    "calculate_entity_quality",
    "get_atom_type_distribution",
    "STRUCTURAL_NOISE_TERMS",
]
