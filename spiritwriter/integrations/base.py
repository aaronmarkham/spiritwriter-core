"""Abstract protocols for pluggable memory providers.

Any memory system (MemPalace, Mem0, Zep, Mastra, etc.) can implement
these protocols to integrate with spiritwriter. The protocols are
designed so each side contributes what it's best at:

- Memory providers contribute: semantic search, retrieval ranking,
  knowledge graphs, compression dialects
- spiritwriter contributes: content addressing, encryption, entity
  resolution, provenance, access control, distribution

Providers implement what they support and declare capabilities via
the `capabilities` property. Consumers check capabilities before calling.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator


class ProviderCapability(str, Enum):
    """What a memory provider can do."""
    SEMANTIC_SEARCH = "semantic_search"       # vector similarity search
    KEYWORD_SEARCH = "keyword_search"         # BM25 / text match
    HYBRID_SEARCH = "hybrid_search"           # combined semantic + keyword
    KNOWLEDGE_GRAPH = "knowledge_graph"       # entity-relationship triples
    TEMPORAL_QUERY = "temporal_query"          # time-filtered retrieval
    ENTITY_DETECTION = "entity_detection"     # extract entities from text
    COMPRESSION = "compression"               # AAAK or similar dialect
    LLM_RERANK = "llm_rerank"                # LLM-based result reranking
    STORAGE = "storage"                       # can persist documents
    DEDUP = "dedup"                           # duplicate detection


@dataclass
class SearchResult:
    """A single retrieval result from a memory provider.

    Provider-agnostic: every memory system returns these, regardless
    of whether it uses ChromaDB, PostgreSQL, or something else.
    """
    document_id: str                   # provider's internal ID
    text: str                          # retrieved content
    score: float                       # relevance (0.0-1.0, higher = better)
    metadata: dict[str, Any] = field(default_factory=dict)
    # Optional spiritwriter fields (populated when shard-backed)
    shard_id: str | None = None        # content address if stored as shard
    scope: str | None = None           # shard scope if applicable
    source: str | None = None          # provider name


@dataclass
class SearchQuery:
    """A retrieval query, provider-agnostic."""
    text: str                          # natural language query
    top_k: int = 10                    # max results
    filters: dict[str, Any] = field(default_factory=dict)
    # Optional: scope/wing/room filtering
    scope: str | None = None
    # Optional: temporal filtering
    after: str | None = None           # ISO timestamp
    before: str | None = None          # ISO timestamp


@dataclass
class Document:
    """A document to store in a memory provider."""
    document_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityTriple:
    """A knowledge graph triple (subject → predicate → object)."""
    subject: str
    predicate: str
    object: str
    valid_from: str | None = None
    valid_to: str | None = None
    source_ref: str | None = None


@dataclass
class ProviderInfo:
    """Metadata about a memory provider."""
    name: str                          # e.g. "mempalace", "mem0", "zep"
    version: str                       # provider version
    description: str
    capabilities: list[ProviderCapability]
    requires_api_key: bool = False     # true for cloud providers
    requires_gpu: bool = False
    local_only: bool = True            # data stays on machine


class RetrievalProvider(ABC):
    """Abstract interface for memory retrieval systems.

    Implement this to make any memory system searchable through
    spiritwriter's integration layer.
    """

    @abstractmethod
    def info(self) -> ProviderInfo:
        """Provider metadata and capabilities."""
        ...

    @property
    def capabilities(self) -> list[ProviderCapability]:
        return self.info().capabilities

    def has_capability(self, cap: ProviderCapability) -> bool:
        return cap in self.capabilities

    @abstractmethod
    def search(self, query: SearchQuery) -> list[SearchResult]:
        """Semantic or hybrid search. Returns ranked results."""
        ...

    def is_available(self) -> bool:
        """Check if the provider is ready (installed, configured, etc.)."""
        return True


class StorageProvider(ABC):
    """Abstract interface for memory storage systems.

    Providers that can persist documents implement this alongside
    RetrievalProvider.
    """

    @abstractmethod
    def store(self, documents: list[Document]) -> list[str]:
        """Store documents. Returns list of document IDs."""
        ...

    @abstractmethod
    def get(self, document_id: str) -> Document | None:
        """Retrieve a document by ID."""
        ...

    @abstractmethod
    def delete(self, document_id: str) -> bool:
        """Delete a document. Returns True if it existed."""
        ...

    @abstractmethod
    def count(self) -> int:
        """Total documents in the store."""
        ...

    def check_duplicate(self, text: str, threshold: float = 0.9) -> str | None:
        """Check if near-duplicate exists. Returns ID or None."""
        return None


class EntityProvider(ABC):
    """Abstract interface for knowledge graph / entity systems.

    Providers with entity-relationship capabilities implement this.
    """

    @abstractmethod
    def add_triple(self, triple: EntityTriple) -> str:
        """Add a knowledge graph triple. Returns triple ID."""
        ...

    @abstractmethod
    def query_entity(
        self, entity: str, as_of: str | None = None
    ) -> list[EntityTriple]:
        """Query all triples for an entity, optionally at a point in time."""
        ...

    @abstractmethod
    def invalidate(
        self, subject: str, predicate: str, obj: str, ended: str | None = None
    ) -> bool:
        """Mark a triple as no longer valid."""
        ...

    def entity_stats(self) -> dict[str, int]:
        """Entity/triple counts."""
        return {}
