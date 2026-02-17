"""Knowledge Project models - Multi-source knowledge base for video production"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any

from .document import DocumentAtom, AtomType


class SourceType(Enum):
    """Types of knowledge sources"""
    PAPER = "paper"
    ARTICLE = "article"
    NOTE = "note"
    DATASET = "dataset"
    URL = "url"


@dataclass
class KnowledgeSource:
    """A single source within a knowledge project"""
    source_id: str
    source_type: SourceType

    # Metadata
    title: str = ""
    authors: List[str] = field(default_factory=list)
    added_at: str = ""
    source_path: Optional[str] = None

    # Reference to its DocumentGraph
    document_id: Optional[str] = None

    # Source-level summaries
    one_sentence: str = ""
    atom_count: int = 0
    page_count: int = 0
    figure_count: int = 0

    # User annotations
    tags: List[str] = field(default_factory=list)
    user_notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type.value,
            "title": self.title,
            "authors": self.authors,
            "added_at": self.added_at,
            "source_path": self.source_path,
            "document_id": self.document_id,
            "one_sentence": self.one_sentence,
            "atom_count": self.atom_count,
            "page_count": self.page_count,
            "figure_count": self.figure_count,
            "tags": self.tags,
            "user_notes": self.user_notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'KnowledgeSource':
        return cls(
            source_id=data["source_id"],
            source_type=SourceType(data["source_type"]),
            title=data.get("title", ""),
            authors=data.get("authors", []),
            added_at=data.get("added_at", ""),
            source_path=data.get("source_path"),
            document_id=data.get("document_id"),
            one_sentence=data.get("one_sentence", ""),
            atom_count=data.get("atom_count", 0),
            page_count=data.get("page_count", 0),
            figure_count=data.get("figure_count", 0),
            tags=data.get("tags", []),
            user_notes=data.get("user_notes", ""),
        )


@dataclass
class CrossSourceLink:
    """A relationship between atoms from different sources"""
    link_id: str
    source_atom_id: str
    target_atom_id: str
    source_source_id: str
    target_source_id: str
    relationship: str  # "supports", "contradicts", "extends", "same_topic"
    confidence: float = 0.5
    created_by: str = "auto"  # "auto" or "user"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "link_id": self.link_id,
            "source_atom_id": self.source_atom_id,
            "target_atom_id": self.target_atom_id,
            "source_source_id": self.source_source_id,
            "target_source_id": self.target_source_id,
            "relationship": self.relationship,
            "confidence": self.confidence,
            "created_by": self.created_by,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CrossSourceLink':
        return cls(
            link_id=data["link_id"],
            source_atom_id=data["source_atom_id"],
            target_atom_id=data["target_atom_id"],
            source_source_id=data["source_source_id"],
            target_source_id=data["target_source_id"],
            relationship=data.get("relationship", "same_topic"),
            confidence=data.get("confidence", 0.5),
            created_by=data.get("created_by", "auto"),
        )


@dataclass
class Note:
    """User-authored note within a knowledge project"""
    note_id: str
    title: str = ""
    content: str = ""
    created_at: str = ""
    updated_at: str = ""

    # Connections to sources/atoms
    related_sources: List[str] = field(default_factory=list)
    related_atoms: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "note_id": self.note_id,
            "title": self.title,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "related_sources": self.related_sources,
            "related_atoms": self.related_atoms,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Note':
        return cls(
            note_id=data["note_id"],
            title=data.get("title", ""),
            content=data.get("content", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            related_sources=data.get("related_sources", []),
            related_atoms=data.get("related_atoms", []),
            tags=data.get("tags", []),
        )


@dataclass
class Connection:
    """User-defined connection between items in the project"""
    connection_id: str
    from_id: str
    to_id: str
    from_type: str = "atom"  # "source", "atom", "note"
    to_type: str = "atom"
    label: str = ""
    relationship: str = ""  # "builds_on", "contradicts", "applies_to", "combines_with"
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "connection_id": self.connection_id,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "from_type": self.from_type,
            "to_type": self.to_type,
            "label": self.label,
            "relationship": self.relationship,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Connection':
        return cls(
            connection_id=data["connection_id"],
            from_id=data["from_id"],
            to_id=data["to_id"],
            from_type=data.get("from_type", "atom"),
            to_type=data.get("to_type", "atom"),
            label=data.get("label", ""),
            relationship=data.get("relationship", ""),
            created_at=data.get("created_at", ""),
        )


@dataclass
class KnowledgeGraph:
    """Unified knowledge graph spanning all sources in a project"""
    project_id: str

    # All atoms from all sources
    atoms: Dict[str, DocumentAtom] = field(default_factory=dict)

    # Source membership: atom_id -> source_id
    atom_sources: Dict[str, str] = field(default_factory=dict)

    # Cross-source links
    cross_links: List[CrossSourceLink] = field(default_factory=list)

    # Indices for fast lookup
    topic_index: Dict[str, List[str]] = field(default_factory=dict)
    entity_index: Dict[str, List[str]] = field(default_factory=dict)

    # Graph-level summaries
    unified_summary: str = ""
    key_themes: List[str] = field(default_factory=list)

    @property
    def atom_count(self) -> int:
        return len(self.atoms)

    @property
    def source_count(self) -> int:
        return len(set(self.atom_sources.values()))

    @property
    def cross_link_count(self) -> int:
        return len(self.cross_links)

    def get_atoms_for_source(self, source_id: str) -> List[DocumentAtom]:
        """Get all atoms belonging to a specific source"""
        return [
            self.atoms[aid] for aid, sid in self.atom_sources.items()
            if sid == source_id and aid in self.atoms
        ]

    def get_shared_topics(self) -> Dict[str, List[str]]:
        """Get topics that appear in atoms from multiple sources"""
        shared = {}
        for topic, atom_ids in self.topic_index.items():
            sources = set(
                self.atom_sources[aid] for aid in atom_ids
                if aid in self.atom_sources
            )
            if len(sources) > 1:
                shared[topic] = atom_ids
        return shared

    def get_shared_entities(self) -> Dict[str, List[str]]:
        """Get entities that appear in atoms from multiple sources"""
        shared = {}
        for entity, atom_ids in self.entity_index.items():
            sources = set(
                self.atom_sources[aid] for aid in atom_ids
                if aid in self.atom_sources
            )
            if len(sources) > 1:
                shared[entity] = atom_ids
        return shared

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "atoms": {aid: atom.to_dict() for aid, atom in self.atoms.items()},
            "atom_sources": self.atom_sources,
            "cross_links": [link.to_dict() for link in self.cross_links],
            "topic_index": self.topic_index,
            "entity_index": self.entity_index,
            "unified_summary": self.unified_summary,
            "key_themes": self.key_themes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'KnowledgeGraph':
        atoms = {}
        for aid, atom_data in data.get("atoms", {}).items():
            atoms[aid] = DocumentAtom(
                atom_id=atom_data["atom_id"],
                atom_type=AtomType(atom_data["atom_type"]),
                content=atom_data.get("content", ""),
                source_page=atom_data.get("source_page"),
                source_location=atom_data.get("source_location"),
                topics=atom_data.get("topics", []),
                entities=atom_data.get("entities", []),
                relationships=atom_data.get("relationships", []),
                importance_score=atom_data.get("importance_score", 0.5),
                caption=atom_data.get("caption"),
                figure_number=atom_data.get("figure_number"),
                data_summary=atom_data.get("data_summary"),
            )

        cross_links = [
            CrossSourceLink.from_dict(link)
            for link in data.get("cross_links", [])
        ]

        return cls(
            project_id=data["project_id"],
            atoms=atoms,
            atom_sources=data.get("atom_sources", {}),
            cross_links=cross_links,
            topic_index=data.get("topic_index", {}),
            entity_index=data.get("entity_index", {}),
            unified_summary=data.get("unified_summary", ""),
            key_themes=data.get("key_themes", []),
        )


@dataclass
class KnowledgeProject:
    """Top-level container for a knowledge base"""
    project_id: str
    name: str
    description: str = ""
    created_at: str = ""
    updated_at: str = ""

    # Sources
    sources: Dict[str, KnowledgeSource] = field(default_factory=dict)

    # User additions
    notes: Dict[str, Note] = field(default_factory=dict)
    connections: List[Connection] = field(default_factory=list)

    # Graph state
    has_knowledge_graph: bool = False

    # Project-level metadata
    tags: List[str] = field(default_factory=list)
    total_atoms: int = 0
    total_figures: int = 0
    total_pages: int = 0

    @property
    def source_count(self) -> int:
        return len(self.sources)

    def add_source(self, source: KnowledgeSource) -> None:
        """Add a source and update aggregate counts"""
        self.sources[source.source_id] = source
        self.total_atoms += source.atom_count
        self.total_figures += source.figure_count
        self.total_pages += source.page_count
        self.updated_at = datetime.now().isoformat()

    def get_source(self, source_id: str) -> Optional[KnowledgeSource]:
        return self.sources.get(source_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sources": {sid: s.to_dict() for sid, s in self.sources.items()},
            "notes": {nid: n.to_dict() for nid, n in self.notes.items()},
            "connections": [c.to_dict() for c in self.connections],
            "has_knowledge_graph": self.has_knowledge_graph,
            "tags": self.tags,
            "total_atoms": self.total_atoms,
            "total_figures": self.total_figures,
            "total_pages": self.total_pages,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'KnowledgeProject':
        sources = {
            sid: KnowledgeSource.from_dict(sdata)
            for sid, sdata in data.get("sources", {}).items()
        }
        notes = {
            nid: Note.from_dict(ndata)
            for nid, ndata in data.get("notes", {}).items()
        }
        connections = [
            Connection.from_dict(cdata)
            for cdata in data.get("connections", [])
        ]

        return cls(
            project_id=data["project_id"],
            name=data["name"],
            description=data.get("description", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            sources=sources,
            notes=notes,
            connections=connections,
            has_knowledge_graph=data.get("has_knowledge_graph", False),
            tags=data.get("tags", []),
            total_atoms=data.get("total_atoms", 0),
            total_figures=data.get("total_figures", 0),
            total_pages=data.get("total_pages", 0),
        )


def generate_id(prefix: str, seed: str) -> str:
    """Generate a short hash ID with prefix (e.g., kb_a1b2c3d4e5f6)"""
    h = hashlib.sha256(seed.encode()).hexdigest()[:12]
    return f"{prefix}_{h}"